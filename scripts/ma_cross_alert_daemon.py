"""Bitget USDT-perp 이동평균 골든/데드 크로스 알림 데몬.

비트겟 거래량 top-N (24h usdtVolume) 유니버스의 각 종목에 대해 **1시간봉**
SMA(fast) / SMA(slow) 를 계산하고, 매 1h 봉 마감 시점에 골든크로스(fast 가
slow 를 상향 돌파) / 데드크로스(하향 돌파) 가 발생하면 텔레그램으로 알림을
보낸다.

설계는 ``scripts/airborne_alert_daemon.py`` 의 ``--venue bitget --mode polling``
경로를 그대로 차용한다 — 같은 유니버스(`get_top_n_symbols`), 같은 1h 캔들 REST
(`bitget bootstrap_history`), 같은 텔레그램 채널(`observability.alerts.notify`),
같은 한국-IP-safe REST 폴링(매 1h 정각 +30s). 신호 평가만 BB 되돌림 발화 대신
이동평균 크로스로 교체한 것.

이 데몬은 **시각적 가이드 알림**이다 — 자동매매로 직접 연결되지 않으며,
주문/리스크 결정을 내리지 않는다 (아키텍처 불변식 #6 준수).

Usage:
    python scripts/ma_cross_alert_daemon.py                 # 비트겟 top-100, SMA 25/200
    python scripts/ma_cross_alert_daemon.py --top-n 50
    python scripts/ma_cross_alert_daemon.py --fast 25 --slow 200
    python scripts/ma_cross_alert_daemon.py --dry-run       # 텔레그램 대신 stdout
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
# _SRC: top-level 패키지 (observability/portfolio/brokers) import 용.
# _ROOT: portfolio.__init__ 이 내부에서 쓰는 `src.brokers.base` 절대 import 용
# (레포 루트가 path 에 없으면 ModuleNotFoundError: No module named 'src').
for _p in (str(_ROOT), str(_SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _autoload_dotenv() -> None:
    """Walk up from cwd / repo root looking for a .env file (mirrors live_run.py)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for candidate in (Path.cwd(), _ROOT, _ROOT.parent):
        env = candidate / ".env"
        if env.exists():
            load_dotenv(env)
            return


_autoload_dotenv()

from observability.alerts import notify  # noqa: E402

log = logging.getLogger("ma_cross_alert_daemon")

# 데몬 버전 — 게이트/표시 의미가 바뀔 때 patch 증가.
DAEMON_VERSION = "v0.1.0"

DEFAULT_TOP_N = 100
DEFAULT_FAST = 25
DEFAULT_SLOW = 200
# slow MA 한 점 + 직전 봉 비교에 slow+2 봉 필요. 여유분 +50 (Bitget v2 candles
# limit max 1000 이라 안전). top-N 전체에 대해 매 폴링마다 이 개수를 REST fetch.
WARMUP_MARGIN = 50
DEFAULT_UNIVERSE_REFRESH_HOURS = 6.0
# 동일 방향 크로스 재알림 억제 창. 크로스는 이산 이벤트라 보통 멀리 떨어져
# 발생하지만, 경계에서 MA 가 진동하며 연속 봉에 교차/역교차를 반복할 때
# 알림 폭주를 막는다. (cooldown 은 종목+방향 별 마지막 발화 봉 open_time 기준.)
COOLDOWN_HOURS = 6
BAR_MS_1H = 3_600_000

CROSS_GOLDEN = "golden"
CROSS_DEATH = "death"


# ── 순수 함수 (단위 테스트 대상) ──────────────────────────────────────────────


def _bitget_bars_to_history(bars: list) -> pd.DataFrame:
    """Bitget ``[KlineEvent]`` → UTC DatetimeIndex + float OHLCV DataFrame.

    Bitget candles REST 는 최신→과거 순서로 올 수 있어 open_time 오름차순 정렬.
    (airborne_alert_daemon._bitget_bars_to_history 와 동일 규약.)
    """
    cols = ["open", "high", "low", "close", "volume"]
    if not bars:
        return pd.DataFrame(columns=cols)
    rows = sorted(bars, key=lambda b: b.open_time)
    idx = pd.to_datetime([b.open_time for b in rows], unit="ms", utc=True)
    return pd.DataFrame(
        {
            "open": [float(b.open) for b in rows],
            "high": [float(b.high) for b in rows],
            "low": [float(b.low) for b in rows],
            "close": [float(b.close) for b in rows],
            "volume": [float(b.volume) for b in rows],
        },
        index=idx,
    )


def detect_cross(close: pd.Series, fast: int, slow: int) -> str | None:
    """마지막으로 *확정된* 봉에서 SMA(fast) × SMA(slow) 크로스를 판정.

    직전 봉과 현재 봉의 두 이동평균 상하 관계를 비교한다:
      - golden: 직전 fast<=slow 이고 현재 fast>slow  (상향 돌파)
      - death : 직전 fast>=slow 이고 현재 fast<slow  (하향 돌파)
    그 외(추세 유지·MA 미확보)는 ``None``.

    SMA 산출에 slow 봉, 직전 봉 비교에 +1 봉이 더 필요하므로 최소 slow+2 봉이
    있어야 두 시점 모두 NaN 이 아니다.
    """
    if fast >= slow:
        raise ValueError(f"fast({fast}) 는 slow({slow}) 보다 작아야 합니다")
    if len(close) < slow + 2:
        return None
    ma_fast = close.rolling(fast).mean()
    ma_slow = close.rolling(slow).mean()
    pf, ps = ma_fast.iloc[-2], ma_slow.iloc[-2]
    cf, cs = ma_fast.iloc[-1], ma_slow.iloc[-1]
    if pd.isna(pf) or pd.isna(ps) or pd.isna(cf) or pd.isna(cs):
        return None
    if pf <= ps and cf > cs:
        return CROSS_GOLDEN
    if pf >= ps and cf < cs:
        return CROSS_DEATH
    return None


def build_alert(
    *, symbol: str, cross: str, close: float, ma_fast: float, ma_slow: float,
    fast: int, slow: int,
) -> tuple[str, str, dict[str, str]]:
    """(title, body, fields) 알림 페이로드 생성. notify(level, title, body, fields)."""
    if cross == CROSS_GOLDEN:
        title = f"🟢✨ 골든크로스 (매수 신호) — {symbol} (1시간봉)"
        direction = "상향 돌파"
    else:
        title = f"🔴💀 데드크로스 (매도 신호) — {symbol} (1시간봉)"
        direction = "하향 돌파"
    body = (
        f"SMA{fast} 가 SMA{slow} 를 {direction}\n"
        f"   현재가: {close:.6g}\n"
        f"   SMA{fast}: {ma_fast:.6g}\n"
        f"   SMA{slow}: {ma_slow:.6g}"
    )
    fields = {
        "symbol": symbol,
        "timeframe": "1h",
        "type": cross,
        f"sma{fast}": f"{ma_fast:.6g}",
        f"sma{slow}": f"{ma_slow:.6g}",
    }
    return title, body, fields


@dataclass
class SymbolState:
    history_1h: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"]
    ))
    # 종목+방향 별 마지막 발화 봉 open_time(ms) — cooldown.
    last_fire_open_time: dict[str, int] = field(default_factory=dict)
    # 마지막으로 평가한 봉 open_time(ms) — 새 봉만 평가하도록.
    last_eval_open_time: int = 0


def _cooldown_ok(state: SymbolState, cross: str, open_ms: int) -> bool:
    last = state.last_fire_open_time.get(cross, 0)
    return open_ms - last >= COOLDOWN_HOURS * BAR_MS_1H


def evaluate_and_dispatch(
    *, symbol: str, state: SymbolState, fast: int, slow: int,
    dry_run: bool, notify_fn=notify,
) -> str | None:
    """state.history_1h 마지막 확정 봉에서 크로스 판정 → 알림. 발화 시 방향 반환."""
    df = state.history_1h
    if len(df) < slow + 2:
        log.debug("%s warmup (%d/%d)", symbol, len(df), slow + 2)
        return None
    cross = detect_cross(df["close"], fast, slow)
    if cross is None:
        return None
    open_ms = int(df.index[-1].timestamp() * 1000)
    if not _cooldown_ok(state, cross, open_ms):
        log.debug("%s %s cross suppressed by cooldown", symbol, cross)
        return None
    state.last_fire_open_time[cross] = open_ms

    closes = df["close"]
    ma_fast = float(closes.rolling(fast).mean().iloc[-1])
    ma_slow = float(closes.rolling(slow).mean().iloc[-1])
    close = float(closes.iloc[-1])
    title, body, fields = build_alert(
        symbol=symbol, cross=cross, close=close,
        ma_fast=ma_fast, ma_slow=ma_slow, fast=fast, slow=slow,
    )
    if dry_run:
        print(f"[DRY] {title}\n  {body}\n  {fields}", flush=True)
    else:
        notify_fn("info", title, body, fields)
    log.info("CROSS %s %s @ close=%.6g sma%d=%.6g sma%d=%.6g",
             symbol, cross, close, fast, ma_fast, slow, ma_slow)
    return cross


def compute_universe_diff(
    prev: list[str], curr: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """``(added, removed, unchanged)`` — airborne_alert_daemon 와 동일 규약."""
    prev_set, curr_set = set(prev), set(curr)
    added = [s for s in curr if s not in prev_set]
    removed = [s for s in prev if s not in curr_set]
    unchanged = [s for s in curr if s in prev_set]
    return added, removed, unchanged


def _next_polling_wakeup(now_dt: datetime) -> datetime:
    """다음 1h 경계 +30s (UTC), now_dt 직후. 봉 마감 finalize 대기 30s 오프셋."""
    candidate = now_dt.replace(minute=0, second=30, microsecond=0)
    if candidate <= now_dt:
        candidate += timedelta(hours=1)
    return candidate


# ── 런타임 (Bitget REST polling) ─────────────────────────────────────────────


async def _fetch_universe(top_n: int) -> list[str]:
    from portfolio.bitget_top_dynamic import get_top_n_symbols
    return get_top_n_symbols(top_n)


async def _bootstrap_1h(symbols: list[str], limit: int) -> dict[str, pd.DataFrame]:
    """top-N 1h 캔들 REST fetch → {symbol: DataFrame}. 실패 심볼은 빈 DF."""
    from brokers.bitget.market_ws import bootstrap_history as bitget_bootstrap
    per_sym = await bitget_bootstrap(
        symbols=symbols, interval="1h", limit=limit, paper=True,
    )
    return {s: _bitget_bars_to_history(per_sym.get(s, [])) for s in symbols}


async def _bootstrap_into_states(
    symbols: list[str], states: dict[str, SymbolState], *, limit: int,
) -> None:
    """added 심볼만 새 SymbolState 로 1h history seed (in-place).

    batch 실패 시 심볼별 개별 재시도로 강등 — 잘못된 심볼 1개가 나머지를
    죽이지 못하게 (airborne_alert_daemon 와 동일 방어).
    """
    if not symbols:
        return
    try:
        boot = await _bootstrap_1h(symbols, limit)
    except Exception as err:  # noqa: BLE001
        log.warning("batch bootstrap failed (%s) — per-symbol 재시도", err)
        boot = {}
        for s in symbols:
            try:
                boot.update(await _bootstrap_1h([s], limit))
            except Exception as e2:  # noqa: BLE001
                log.warning("bootstrap skip %s — %s", s, e2)
    for s in symbols:
        st = SymbolState()
        st.history_1h = boot.get(s, pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"]))
        if not st.history_1h.empty:
            st.last_eval_open_time = int(st.history_1h.index[-1].timestamp() * 1000)
        states[s] = st


async def run_daemon(
    *,
    top_n: int = DEFAULT_TOP_N,
    fast: int = DEFAULT_FAST,
    slow: int = DEFAULT_SLOW,
    dry_run: bool = False,
    universe_refresh_hours: float = DEFAULT_UNIVERSE_REFRESH_HOURS,
) -> None:
    """매 1h 경계 +30s 에 깨어나 top-N 1h 캔들을 REST fetch, 새 봉마다 크로스 평가.

    유니버스는 ``universe_refresh_hours`` 마다 재계산 (default 6h). removed 종목
    state 는 drop, added 종목은 fetch 후 추가 — unchanged 종목의 cooldown/마지막
    평가 봉은 보존된다.
    """
    if fast >= slow:
        raise ValueError(f"fast({fast}) 는 slow({slow}) 보다 작아야 합니다")
    limit = slow + WARMUP_MARGIN
    states: dict[str, SymbolState] = {}
    prev_universe: list[str] = []
    last_universe_refresh = 0.0
    refresh_secs = universe_refresh_hours * 3600 if universe_refresh_hours > 0 else None

    log.info("daemon %s — bitget top-%d, SMA %d/%d on 1h", DAEMON_VERSION, top_n, fast, slow)

    while True:
        now_loop = asyncio.get_event_loop().time()
        need_refresh = (
            not prev_universe
            or (refresh_secs is not None and now_loop - last_universe_refresh >= refresh_secs)
        )
        if need_refresh:
            log.info("fetching bitget top-%d universe ...", top_n)
            universe = await _fetch_universe(top_n)
            if not universe:
                log.error("empty universe — retrying in 60s")
                await asyncio.sleep(60)
                continue
            added, removed, unchanged = compute_universe_diff(prev_universe, universe)
            if prev_universe:
                log.info("universe refresh — added=%s removed=%s unchanged=%d",
                         added, removed, len(unchanged))
            else:
                log.info("initial universe (top-%d, %d symbols): %s",
                         top_n, len(universe), universe)
            for sym in removed:
                states.pop(sym, None)
            await _bootstrap_into_states(added, states, limit=limit)
            prev_universe = universe
            last_universe_refresh = now_loop
            log.info("states current: %d symbols seeded", len(states))

        now_dt = datetime.now(timezone.utc)
        next_wakeup = _next_polling_wakeup(now_dt)
        wait_secs = (next_wakeup - now_dt).total_seconds()
        log.info("polling: next cycle at %s UTC (%.0fs sleep)",
                 next_wakeup.strftime("%H:%M:%S"), wait_secs)
        await asyncio.sleep(wait_secs)

        log.info("polling cycle start — %d symbols", len(prev_universe))
        try:
            poll = await _bootstrap_1h(prev_universe, limit)
        except Exception as exc:  # noqa: BLE001
            log.error("polling fetch failed: %s — retrying next cycle", exc)
            continue

        fire_count = 0
        for sym in prev_universe:
            state = states.get(sym)
            if state is None:
                continue
            new_1h = poll.get(sym)
            if new_1h is None or new_1h.empty:
                continue
            new_last_ms = int(new_1h.index[-1].timestamp() * 1000)
            state.history_1h = new_1h
            if new_last_ms <= state.last_eval_open_time:
                continue  # 아직 새 봉 없음
            state.last_eval_open_time = new_last_ms
            if evaluate_and_dispatch(
                symbol=sym, state=state, fast=fast, slow=slow, dry_run=dry_run,
            ) is not None:
                fire_count += 1
        log.info("polling cycle complete — %d cross alerts", fire_count)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bitget USDT-perp 이동평균 골든/데드 크로스 알림 데몬 (1시간봉, Telegram)",
    )
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N,
                        help=f"비트겟 거래량 상위 N 종목 (default {DEFAULT_TOP_N})")
    parser.add_argument("--fast", type=int, default=DEFAULT_FAST,
                        help=f"빠른 SMA 기간 (default {DEFAULT_FAST})")
    parser.add_argument("--slow", type=int, default=DEFAULT_SLOW,
                        help=f"느린 SMA 기간 (default {DEFAULT_SLOW})")
    parser.add_argument("--dry-run", action="store_true",
                        help="텔레그램 대신 stdout 으로 출력")
    parser.add_argument(
        "--universe-refresh-hours", type=float,
        default=DEFAULT_UNIVERSE_REFRESH_HOURS,
        help=f"top-N 유니버스 재계산 주기 (default {DEFAULT_UNIVERSE_REFRESH_HOURS}h, 0=고정)",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    log.info("ma cross alert daemon %s", DAEMON_VERSION)

    try:
        asyncio.run(run_daemon(
            top_n=args.top_n,
            fast=args.fast,
            slow=args.slow,
            dry_run=args.dry_run,
            universe_refresh_hours=args.universe_refresh_hours,
        ))
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
