"""Live universe-scanner: HTS 검색식 hybrid (단타/5분대기/스윙 OR 합성) — #230.

Per-symbol entry rule:
    매 1분봉 시점에 DTS|WAIT5M|SWING 검색식 평가 → 어느 1개라도 통과 시 buy.
    시간대 게이트: KST ≤ max_entry_hour (default 10:30).

Exit 는 ``LivePositionRiskManager`` 가 담당 — strategy 는 sell signal 발행 안 함.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar, Optional

import pandas as pd

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from src.screeners.hts_cond import DailyScreeningInputs
from src.screeners.hts_cond.dts import ThreeMinBar
from src.screeners.hts_cond.hybrid import evaluate_hybrid_or

log = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


class LiveHtsHybrid(LiveScannerMixin):
    """Stateless per-symbol HTS hybrid screener (DTS OR WAIT5M OR SWING).

    한국 HTS 조건검색식 3종 (단타·5분대기·스윙) 을 OR 합성하여 1개 strategy 로 운영.
    일간 조건 (A~G) + 분봉 H 조건 (DTS 3분봉 20MA / WAIT5M 정적 VI 근접율) 평가.

    Stop/TP (LiveScannerMixin):
        - stop_loss_pct = 0.02 (-2% 손절)
        - take_profit_pct = 0.02 (+2% 익절)
        - trailing_stop_pct = None
    """

    MIN_HISTORY: ClassVar[int] = 30   # 3분봉 10봉 × 3분 + 버퍼

    stop_loss_pct: ClassVar[float] = 0.02
    take_profit_pct: ClassVar[float] = 0.02
    trailing_stop_pct: ClassVar[float | None] = None

    def __init__(
        self,
        *,
        default_size: float = 0.05,
        max_entry_hour: float = 10.5,
        daily_cache_dir: Optional[str] = None,
    ) -> None:
        """
        Args:
            default_size: fraction-of-equity per signal (0.05 = 5%).
            max_entry_hour: KST 시간대 게이트 (default 10.5 = 10:30 까지만 진입).
            daily_cache_dir: KRX 일봉 parquet 캐시 경로. None 이면 자동 탐색.
        """
        if not 0 < default_size <= 1.0:
            raise ValueError(f"default_size must be in (0, 1], got {default_size}")
        self.default_size = default_size
        self.max_entry_hour = max_entry_hour
        if daily_cache_dir is None:
            # WORKTREE/data/cache/krx_daily 또는 PROJECT_ROOT/data/cache/krx_daily
            here = Path(__file__).resolve().parents[3]  # repo root
            self.daily_cache_dir = here / "data" / "cache" / "krx_daily"
        else:
            self.daily_cache_dir = Path(daily_cache_dir)
        self._daily_cache_by_sym: dict[str, dict | None] = {}

    # -- helpers --------------------------------------------------------

    def _load_daily_snapshot(self, symbol: str) -> dict | None:
        """심볼의 daily cache → (prev_close, MA5/20/60, vol_5d_cumsum). 캐싱."""
        if symbol in self._daily_cache_by_sym:
            return self._daily_cache_by_sym[symbol]
        p = self.daily_cache_dir / f"{symbol}.parquet"
        if not p.exists():
            self._daily_cache_by_sym[symbol] = None
            return None
        try:
            df = pd.read_parquet(p)
        except Exception as e:
            log.debug("daily cache read fail %s: %s", symbol, e)
            self._daily_cache_by_sym[symbol] = None
            return None
        # 오늘 row 제외 (daily refresh 가 적재한 경우 대비)
        try:
            today = datetime.now(KST).date()
            if len(df) > 0 and hasattr(df.index, "date"):
                df = df[df.index.date < today]
        except Exception:
            pass
        if len(df) < 60:
            self._daily_cache_by_sym[symbol] = None
            return None
        closes = df["close"].astype(float)
        vols = df["volume"].astype(int)
        snap = {
            "prev_close": float(closes.iloc[-1]),
            "prev_close_2": float(closes.iloc[-2]),
            "ma5": float(closes.tail(5).mean()),
            "ma20": float(closes.tail(20).mean()),
            "ma60": float(closes.tail(60).mean()),
            "vol_5d_cumsum": int(vols.tail(5).sum()),
        }
        self._daily_cache_by_sym[symbol] = snap
        return snap

    @staticmethod
    def _resample_3min(history: pd.DataFrame) -> list[ThreeMinBar]:
        if history.empty or not hasattr(history.index, "freq"):
            try:
                h = history.copy()
                if not isinstance(h.index, pd.DatetimeIndex):
                    return []
                resampled = h.resample("3min").agg({"close": "last"}).dropna()
                return [ThreeMinBar(close=float(c)) for c in resampled["close"]]
            except Exception:
                return []
        return []

    # -- AsyncStrategy --------------------------------------------------

    async def on_bar(self, ctx: object) -> Signal | None:
        # ctx 는 dict-shaped — momo_kis_v1 / breakout_donchian 컨벤션과 일치
        snap = ctx["market_snapshot"]  # type: ignore[index]
        symbol = snap.get("symbol") if isinstance(snap, dict) else None
        history: pd.DataFrame | None = snap.get("history") if isinstance(snap, dict) else None
        if not symbol or history is None or len(history) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="warmup")

        # 시간대 게이트 (KST)
        try:
            ts = history.index[-1]
            if hasattr(ts, "tz_convert"):
                ts_kst = ts.tz_convert("Asia/Seoul")
            elif getattr(ts, "tzinfo", None) is None:
                ts_kst = pd.Timestamp(ts, tz="UTC").tz_convert("Asia/Seoul")
            else:
                ts_kst = ts
            hour_decimal = ts_kst.hour + ts_kst.minute / 60.0
        except Exception:
            hour_decimal = None
        if hour_decimal is not None and hour_decimal > self.max_entry_hour:
            return Signal(
                action="hold", size=0.0,
                reason=f"time_gate:hour={hour_decimal:.2f}",
            )

        # 일간 스냅샷
        daily = self._load_daily_snapshot(str(symbol))
        if daily is None:
            return Signal(action="hold", size=0.0, reason="no_daily_cache")

        # 평가 시점 입력 구축
        try:
            today_close = float(history["close"].iloc[-1])
            cumvol = int(history["volume"].astype(int).sum())
        except Exception as e:
            return Signal(action="hold", size=0.0, reason=f"bad_history:{type(e).__name__}")

        screening = DailyScreeningInputs(
            symbol=str(symbol),
            prev_close=daily["prev_close"],
            prev_close_2=daily["prev_close_2"],
            today_close=today_close,
            today_volume=cumvol,
            vol_5d_cumsum=daily["vol_5d_cumsum"] + cumvol,
            power_ratio=100.0,  # placeholder — KIS tday_rltv 분봉 시점별 재구성 후속 이슈
            ma5=daily["ma5"], ma20=daily["ma20"], ma60=daily["ma60"],
        )

        bars_3m = self._resample_3min(history)
        result = evaluate_hybrid_or(screening, bars_3m, current_price=today_close)
        if not result.passes:
            return Signal(
                action="hold", size=0.0,
                reason=f"no_signal:dts={result.detail['dts']},"
                       f"wait5m={result.detail['wait5m']},"
                       f"swing={result.detail['swing']}",
            )

        return Signal(
            action="buy",
            size=self.default_size,
            reason=f"hts_hybrid:{result.triggered_by}",
        )
