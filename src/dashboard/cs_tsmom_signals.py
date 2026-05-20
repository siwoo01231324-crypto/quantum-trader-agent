"""Dashboard 자체 cs-tsmom-crypto-daily 신호 계산 (2026-05-20).

production cs_tsmom_crypto_daily 의 broker wiring 과 무관하게 대시보드 서버가
직접 매일 USDT-perp top-30 일봉 fetch → 12-1m momentum score → cross-sectional
top-N ranking → 어제 대비 BUY/EXIT 신호 도출. `/cs-tsmom` 페이지에 표로 표시.

Pine Script (cs-tsmom-crypto-daily 12-1m TS-Momentum) 와 *동일* score 정의를 쓰되
여기는 30종목 cross-sectional ranking 까지 한다 — Pine Script 가 못 하는 절반의
알파(랭킹) 가 대시보드에서 보이는 것. 두 시각화 (TV 차트 line + 대시보드 랭킹표)
가 같은 score 로 일관.

  score(t) = log(close[t-21] / close[t-252])
  eligible = score > 0  AND  rolling-60d 평균 quote_vol >= 1e7 USDT
  top_n    = eligible 중 score 상위 10
  signal   = today.in_top vs yesterday.in_top → ENTER / EXIT / HOLD / OUT

production 의 ``backtest.strategies.cs_tsmom_kr_daily.score_panel`` 과 비트단위
동일한 식 (crypto 버전이 그 함수를 재export 함). 백테스트와 대시보드/TV 가 모두
같은 수식 → 디버깅·검증 일관성 보장.
"""
from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# production.yaml 의 cs-tsmom-crypto-daily kwargs 와 동일 default
DEFAULT_UNIVERSE_SIZE = 30
DEFAULT_TOP_N = 10
DEFAULT_LONG_LB = 252
DEFAULT_SKIP_LB = 21
DEFAULT_MIN_QUOTE_VOL = 10_000_000   # 1천만 USDT
DEFAULT_LIQUIDITY_WIN = 60            # rolling-60d 평균 quote_vol

_CACHE_TTL_SEC = 3600                 # 1시간 — score 는 일봉 close 마다만 바뀜


def compute_signals_from_panels(
    closes: pd.DataFrame,
    quote_vol: pd.DataFrame,
    *,
    long_lb: int = DEFAULT_LONG_LB,
    skip_lb: int = DEFAULT_SKIP_LB,
    top_n: int = DEFAULT_TOP_N,
    min_quote_vol: float = DEFAULT_MIN_QUOTE_VOL,
    liquidity_win: int = DEFAULT_LIQUIDITY_WIN,
) -> list[dict]:
    """30종목 closes/quote_vol panel → 오늘 기준 per-symbol 신호 dict 리스트.

    Returns (sorted by score desc — 양수만, 음수는 score asc 로 뒤에):
        [
          {symbol, last_close, last_ts, score, rank, in_top_today, in_top_yday,
           liquid, signal: "ENTER"|"EXIT"|"HOLD"|"OUT"},
          ...
        ]
    """
    if len(closes) < long_lb + 2:
        return []  # warmup 부족

    avg_qv = quote_vol.rolling(liquidity_win, min_periods=20).mean()

    def _rank_row(i: int) -> pd.Series:
        """i 시점 score 패널에서 top_n in-set boolean Series 반환."""
        c_skip = closes.iloc[i - skip_lb]
        c_long = closes.iloc[i - long_lb]
        score = np.log(c_skip / c_long)
        liquid = avg_qv.iloc[i] >= min_quote_vol
        eligible = score[liquid & score.notna() & (score > 0)]
        in_top = pd.Series(False, index=closes.columns)
        if not eligible.empty:
            picks = eligible.nlargest(top_n).index
            in_top.loc[picks] = True
        return in_top, score, liquid

    i_today = len(closes) - 1
    in_top_today, score_today, liquid_today = _rank_row(i_today)
    in_top_yday, _, _ = _rank_row(i_today - 1)
    last_ts = closes.index[i_today]
    last_close = closes.iloc[i_today]

    # rank: score 양수 중 순위 (1 = 최고), 음수/NaN 은 None
    ranks = score_today.where(score_today > 0).rank(ascending=False, method="min")

    rows: list[dict] = []
    for sym in closes.columns:
        s = float(score_today.get(sym, float("nan")))
        is_today = bool(in_top_today.get(sym, False))
        is_yday = bool(in_top_yday.get(sym, False))
        if is_today and not is_yday:
            sig = "ENTER"
        elif not is_today and is_yday:
            sig = "EXIT"
        elif is_today and is_yday:
            sig = "HOLD"
        else:
            sig = "OUT"
        rank_v = ranks.get(sym)
        rank_i: int | None = int(rank_v) if pd.notna(rank_v) else None
        rows.append({
            "symbol": str(sym),
            "last_close": float(last_close.get(sym, float("nan"))),
            "last_ts": last_ts.isoformat() if hasattr(last_ts, "isoformat") else str(last_ts),
            "score": s if not (s != s) else None,            # NaN → None
            "rank": rank_i,
            "in_top_today": is_today,
            "in_top_yday": is_yday,
            "liquid": bool(liquid_today.get(sym, False)),
            "signal": sig,
        })
    # 정렬: in_top 우선, 그 다음 score desc; nan/음수는 뒤로.
    rows.sort(key=lambda r: (
        not r["in_top_today"],
        -(r["score"] if r["score"] is not None else -1e9),
    ))
    return rows


@dataclass
class CsTsmomState:
    available: bool = False
    reason: str = ""
    fetched_at: str | None = None
    universe_size: int = 0
    rows: list[dict] = field(default_factory=list)


class CsTsmomComputer:
    """30종목 fetch + signals 계산 + 1시간 TTL 캐시 (single-flight 락).

    재계산은 ``compute()`` 호출 시 TTL 초과면 자동. ``compute(force=True)`` 로
    강제. 첫 호출은 fetch 비용(~30 REST calls, ~10초) 큼 — 이후엔 in-memory
    캐시. 디스크 캐시는 bench helper 의 parquet 캐시 (재시작 후에도 빠름).
    """

    def __init__(self, *, ttl_sec: float = _CACHE_TTL_SEC) -> None:
        self._ttl = ttl_sec
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._state: CsTsmomState | None = None
        self._state_at: datetime | None = None

    def _fresh(self) -> CsTsmomState | None:
        if self._state is None or self._state_at is None:
            return None
        age = (datetime.now(timezone.utc) - self._state_at).total_seconds()
        return self._state if age < self._ttl else None

    def peek(self) -> CsTsmomState | None:
        with self._lock:
            return self._state  # past-TTL OK for last-known-good UI

    def compute(self, force: bool = False) -> CsTsmomState:
        if not force:
            with self._lock:
                fresh = self._fresh()
                if fresh is not None:
                    return fresh
        with self._refresh_lock:
            if not force:
                with self._lock:
                    fresh = self._fresh()
                    if fresh is not None:
                        return fresh
            new = self._refresh()
            with self._lock:
                self._state = new
                self._state_at = datetime.now(timezone.utc)
            return new

    def _refresh(self) -> CsTsmomState:
        try:
            # bench_cs_tsmom_crypto 의 fetch + panel build 헬퍼 재사용 (parquet 캐시 포함).
            repo_root = Path(__file__).resolve().parents[2]
            scripts_dir = str(repo_root / "scripts")
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            import importlib
            bench = importlib.import_module("bench_cs_tsmom_crypto")
            symbols = bench.fetch_top_universe(DEFAULT_UNIVERSE_SIZE)
            now = datetime.now(timezone.utc)
            start = (now - timedelta(days=DEFAULT_LONG_LB + 100)).strftime("%Y-%m-%d")
            end = now.strftime("%Y-%m-%d")
            panels = bench.fetch_universe(symbols, start, end, refresh=False)
            if not panels:
                return CsTsmomState(available=False, reason="no panels fetched")
            closes, qv = bench.build_panels(panels)
            rows = compute_signals_from_panels(closes, qv)
            return CsTsmomState(
                available=True,
                fetched_at=now.isoformat(),
                universe_size=len(symbols),
                rows=rows,
            )
        except Exception as err:  # noqa: BLE001 — dashboard 절대 500 금지
            logger.warning("cs_tsmom refresh failed: %s", err)
            return CsTsmomState(
                available=False,
                reason=f"{type(err).__name__}: {err}",
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
