"""Live USDT-perp Airborne v1.1 alert daemon.

Streams Binance USDM Futures 1h/5m klines for the top-N USDT-perp universe,
evaluates Airborne BB-reversal v1.1 long+short signals on each confirmed 1h
bar, and pushes Telegram alerts via :func:`observability.alerts.notify`.

The markPrice@arr@1s stream is also subscribed but currently consumed silently
(MVP: kline-only signal evaluation). Phase 2 will add 5m trailing-stop
warnings keyed off mark_price ticks.

Strategy family note: the entire Airborne BB-reversal family (v1, v1.1, v2,
v3) is ``status: rejected`` in 5y multi-regime backtest (PF<1). These alerts
are a **visual guide reproduction** of the external-lecture indicator — do
not depend on them for auto-trading. See
``docs/specs/strategies/airborne-family-overview.md``.

Usage:
    python scripts/airborne_alert_daemon.py --top-n 50
    python scripts/airborne_alert_daemon.py --top-n 5 --dry-run
    python scripts/airborne_alert_daemon.py --testnet --top-n 3 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


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

import signals  # noqa: E402
from brokers.binance.market_ws import (  # noqa: E402
    BinanceMarketDataStream,
    KlineEvent,
    MarkPriceEvent,
    REST_BASE_LIVE,
    REST_BASE_TESTNET,
    WS_BASE_LIVE,
    WS_BASE_TESTNET,
    bootstrap_history,
)
from observability.alerts import notify  # noqa: E402
from signals.airborne_bb_reversal import (  # noqa: E402
    AirborneSetup,
    evaluate_long_fire_v11,
    evaluate_short_fire_v11,
)
from universe.binance_futures_snapshot import fetch_futures_24h_snapshot  # noqa: E402
from universe.binance_top import top_n_by_volume  # noqa: E402

log = logging.getLogger("airborne_alert_daemon")

BB_WINDOW = 20
BB_STD = 2.0
MAX_LOOKBACK = 50
MIN_HISTORY = BB_WINDOW + 2
DEFAULT_TOP_N = 50
COOLDOWN_HOURS = 4  # suppress repeat (symbol, side) fires within this window
BAR_MS_1H = 3_600_000


@dataclass
class SymbolState:
    history_1h: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"]
    ))
    history_5m: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"]
    ))
    last_fire_open_time: dict[str, int] = field(default_factory=dict)


def _append_bar(df: pd.DataFrame, ev: KlineEvent, *, max_bars: int) -> pd.DataFrame:
    ts = pd.Timestamp(ev.open_time, unit="ms", tz="UTC")
    new_row = pd.DataFrame(
        {"open": [ev.open], "high": [ev.high], "low": [ev.low],
         "close": [ev.close], "volume": [ev.volume]},
        index=[ts],
    )
    if ts in df.index:
        df.loc[ts] = new_row.iloc[0]
        return df
    df = new_row if df.empty else pd.concat([df, new_row])
    if len(df) > max_bars:
        df = df.iloc[-max_bars:]
    return df


def _five_min_trend_preview(history_5m: pd.DataFrame, lookback: int = 3) -> str:
    if len(history_5m) < lookback + 1:
        return "n/a"
    diffs = history_5m["close"].iloc[-lookback:].diff().dropna()
    if (diffs > 0).all():
        return "ascending"
    if (diffs < 0).all():
        return "descending"
    return "mixed"


def build_alert_payload(
    *, symbol: str, side: str, ev: KlineEvent, setup: AirborneSetup,
    trigger: float, history_5m: pd.DataFrame,
) -> dict[str, str]:
    return {
        "symbol": symbol,
        "timeframe": "1h",
        "side": side,
        "fire_close": f"{ev.close:.6g}",
        "trigger": f"{trigger:.6g}",
        "base": f"{setup.base:.6g}",
        "extreme": f"{setup.extreme:.6g}",
        "5m_preview": _five_min_trend_preview(history_5m),
        "note": "v1.1 reproduction — family rejected; visual guide only",
    }


def _cooldown_ok(state: SymbolState, side: str, ev_open_time: int) -> bool:
    last = state.last_fire_open_time.get(side, 0)
    return ev_open_time - last >= COOLDOWN_HOURS * BAR_MS_1H


def dispatch_fire(
    *, symbol: str, side: str, state: SymbolState, ev: KlineEvent,
    setup: AirborneSetup, trigger: float, dry_run: bool,
    notify_fn=notify,
) -> bool:
    """Apply cooldown, build payload, and emit alert. Returns True if dispatched.

    Pure dispatcher (testable) — takes the notify callable as a kwarg so tests
    can inject a spy.
    """
    if not _cooldown_ok(state, side, ev.open_time):
        log.debug("%s %s fire suppressed by cooldown", symbol, side)
        return False
    state.last_fire_open_time[side] = ev.open_time

    payload = build_alert_payload(
        symbol=symbol, side=side, ev=ev, setup=setup, trigger=trigger,
        history_5m=state.history_5m,
    )
    title = f"Airborne v1.1 {side.upper()} — {symbol} (1h)"
    body = (
        f"40% retrace fired at {ev.close:.6g} (trigger {trigger:.6g}, "
        f"base {setup.base:.6g}, extreme {setup.extreme:.6g})"
    )
    if dry_run:
        print(f"[DRY] {title}\n  {body}\n  {payload}", flush=True)
    else:
        notify_fn("info", title, body, payload)
    log.info("FIRE %s %s @ close=%.6g trigger=%.6g", symbol, side, ev.close, trigger)
    return True


def evaluate_and_dispatch(
    *, symbol: str, state: SymbolState, ev: KlineEvent, dry_run: bool,
    notify_fn=notify,
) -> tuple[bool, bool]:
    """Run v1.1 long+short evaluators and dispatch fires. Returns (long_fired, short_fired)."""
    df = state.history_1h
    if len(df) < MIN_HISTORY:
        log.debug("%s warmup (%d/%d)", symbol, len(df), MIN_HISTORY)
        return False, False
    bb = signals.compute("bollinger", close=df["close"], window=BB_WINDOW, n_std=BB_STD)
    bb_lower = bb["lower"]
    bb_upper = bb["upper"]

    long_fires, long_setup, long_trig = evaluate_long_fire_v11(
        history=df, bb_lower=bb_lower, max_lookback=MAX_LOOKBACK,
    )
    short_fires, short_setup, short_trig = evaluate_short_fire_v11(
        history=df, bb_upper=bb_upper, max_lookback=MAX_LOOKBACK,
    )

    long_dispatched = short_dispatched = False
    if long_fires and long_setup is not None:
        long_dispatched = dispatch_fire(
            symbol=symbol, side="long", state=state, ev=ev,
            setup=long_setup, trigger=long_trig,
            dry_run=dry_run, notify_fn=notify_fn,
        )
    if short_fires and short_setup is not None:
        short_dispatched = dispatch_fire(
            symbol=symbol, side="short", state=state, ev=ev,
            setup=short_setup, trigger=short_trig,
            dry_run=dry_run, notify_fn=notify_fn,
        )
    return long_dispatched, short_dispatched


DEFAULT_UNIVERSE_REFRESH_HOURS = 6.0


def compute_universe_diff(
    prev: list[str], curr: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Return ``(added, removed, unchanged)`` between two universe lists.

    Pure function. ``added`` and ``unchanged`` follow ``curr`` ordering;
    ``removed`` follows ``prev`` ordering.
    """
    prev_set = set(prev)
    curr_set = set(curr)
    added = [s for s in curr if s not in prev_set]
    removed = [s for s in prev if s not in curr_set]
    unchanged = [s for s in curr if s in prev_set]
    return added, removed, unchanged


async def _bootstrap_into_states(
    symbols: list[str],
    states: dict[str, SymbolState],
    *,
    rest_base_url: str,
) -> None:
    """REST-bootstrap ``symbols`` history into ``states`` (in-place).

    Each new symbol gets a fresh :class:`SymbolState` with 1h+5m history
    seeded. Existing entries in ``states`` are not touched — caller is
    expected to have already removed stale entries.
    """
    if not symbols:
        return
    boot = await bootstrap_history(
        symbols=symbols, intervals=("1h", "5m"),
        limit_per_interval={"1h": 100, "5m": 50},
        base_url=rest_base_url,
    )
    for s in symbols:
        st = SymbolState()
        st.history_1h = boot.get(s, {}).get("1h", st.history_1h)
        st.history_5m = boot.get(s, {}).get("5m", st.history_5m)
        states[s] = st


async def _consume_stream(
    stream: BinanceMarketDataStream,
    states: dict[str, SymbolState],
    dry_run: bool,
) -> None:
    """Drain ``stream`` until exhausted or cancelled. Dispatches alerts on
    each confirmed 1h close for symbols currently in ``states``.

    MarkPrice events are consumed silently (MVP). Events for symbols that
    have been removed from the universe mid-cycle are dropped without
    error (lookup miss in ``states``).
    """
    async for ev in stream.stream():
        if isinstance(ev, MarkPriceEvent):
            continue
        sym = ev.symbol
        state = states.get(sym)
        if state is None:
            continue
        if ev.interval == "5m":
            if ev.is_closed:
                state.history_5m = _append_bar(state.history_5m, ev, max_bars=100)
            continue
        if ev.interval == "1h":
            if not ev.is_closed:
                continue
            state.history_1h = _append_bar(state.history_1h, ev, max_bars=200)
            evaluate_and_dispatch(symbol=sym, state=state, ev=ev, dry_run=dry_run)


async def run_daemon(
    *,
    top_n: int = DEFAULT_TOP_N,
    dry_run: bool = False,
    ws_base_url: str = WS_BASE_LIVE,
    rest_base_url: str = REST_BASE_LIVE,
    universe_refresh_hours: float = DEFAULT_UNIVERSE_REFRESH_HOURS,
) -> None:
    """Run the alert daemon.

    If ``universe_refresh_hours > 0`` the universe is re-computed on that
    cadence and the WS stream is rebuilt to reflect added / removed
    symbols. Removed-symbol state is dropped; added-symbol history is
    REST-bootstrapped before subscription. Cooldown state for unchanged
    symbols is preserved across cycles.

    Passing ``universe_refresh_hours <= 0`` disables periodic refresh
    (legacy behaviour: universe locked at startup, stream runs forever).
    """
    states: dict[str, SymbolState] = {}
    prev_universe: list[str] = []
    refresh_secs: float | None = (
        universe_refresh_hours * 3600 if universe_refresh_hours > 0 else None
    )

    while True:
        log.info("fetching 24h snapshot from %s ...", rest_base_url)
        snap = await fetch_futures_24h_snapshot(base_url=rest_base_url)
        universe = top_n_by_volume(snap, n=top_n)
        if not universe:
            log.error("empty universe — retrying in 60s")
            await asyncio.sleep(60)
            continue

        added, removed, unchanged = compute_universe_diff(prev_universe, universe)
        if prev_universe:
            log.info(
                "universe refresh — added=%s removed=%s unchanged=%d",
                added, removed, len(unchanged),
            )
        else:
            log.info(
                "initial universe (top-%d USDT-perp, %d symbols): %s",
                top_n, len(universe), universe,
            )

        for sym in removed:
            states.pop(sym, None)
        await _bootstrap_into_states(added, states, rest_base_url=rest_base_url)
        prev_universe = universe
        log.info("states current: %d symbols seeded", len(states))

        stream = BinanceMarketDataStream(
            symbols=universe, intervals=("1h", "5m"),
            base_url=ws_base_url,
            include_mark_price_arr=True,
        )

        if refresh_secs is None:
            log.info(
                "opening WS (%d streams) — universe refresh disabled",
                stream.stream_count,
            )
            await _consume_stream(stream, states, dry_run)
            return  # stream exhausted (only happens on hard error)

        log.info(
            "opening WS (%d streams) — universe refresh in %.1fh",
            stream.stream_count, universe_refresh_hours,
        )
        consume_task = asyncio.create_task(
            _consume_stream(stream, states, dry_run),
            name="airborne-stream-consumer",
        )
        try:
            await asyncio.wait_for(
                asyncio.shield(consume_task), timeout=refresh_secs,
            )
        except asyncio.TimeoutError:
            log.info(
                "universe refresh cycle triggered (%.1fh elapsed)",
                universe_refresh_hours,
            )
        finally:
            await stream.close()
            consume_task.cancel()
            try:
                await consume_task
            except (asyncio.CancelledError, Exception):
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Airborne v1.1 USDT-perp alert daemon (Binance Futures, Telegram)",
    )
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N,
                        help=f"Universe size (default {DEFAULT_TOP_N})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print alerts to stdout instead of Telegram")
    parser.add_argument("--testnet", action="store_true",
                        help="Use Binance testnet REST + WS endpoints")
    parser.add_argument(
        "--universe-refresh-hours", type=float,
        default=DEFAULT_UNIVERSE_REFRESH_HOURS,
        help=(
            f"Re-compute top-N universe on this cadence (default "
            f"{DEFAULT_UNIVERSE_REFRESH_HOURS}h). Pass 0 to disable "
            "(universe locks at startup, legacy behaviour)."
        ),
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    ws_base = WS_BASE_TESTNET if args.testnet else WS_BASE_LIVE
    rest_base = REST_BASE_TESTNET if args.testnet else REST_BASE_LIVE

    try:
        asyncio.run(run_daemon(
            top_n=args.top_n,
            dry_run=args.dry_run,
            ws_base_url=ws_base,
            rest_base_url=rest_base,
            universe_refresh_hours=args.universe_refresh_hours,
        ))
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
