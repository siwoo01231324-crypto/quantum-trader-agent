#!/usr/bin/env python3
"""Shadow Run - Swing Strategy Paper Trading CLI (Issues #175, #143).

Runs s2c-voltarget / s4-funding / r4-switch strategy against live 4h Binance
Futures candles, submitting signals to PaperBroker and recording all fills to WAL.

Usage:
    python scripts/shadow_run_swing.py --strategy r4-switch --symbol BTCUSDT
    python scripts/shadow_run_swing.py --strategy s2c-voltarget --symbol BTCUSDT
    python scripts/shadow_run_swing.py --strategy s4-funding --symbol BTCUSDT
    python scripts/shadow_run_swing.py --strategy r4-switch --symbol BTCUSDT --max-bars 3

WAL location: logs/shadow/{run_id}/wal.jsonl

Strategy variants:
    s2c-voltarget : s2_donchian_voltarget  (entry=20, exit=10, vol_target=0.15)
    s4-funding    : s4_funding_carry        (threshold=-0.005%)
    r4-switch     : threshold-regime switch (return_lookback=180; #173 BEST,
                    Sharpe 1.218 / MDD -9.7% in 5y BTCUSDT@4h bench)

Cron example (Phase 1 #143 default — every 4h at bar close):
    0 1,5,9,13,17,21 * * * python scripts/shadow_run_swing.py --strategy r4-switch --symbol BTCUSDT --max-bars 1
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))

# Import paper_adapter without triggering src/backtest/__init__ (which requires
# the optional 'portfolio' package not installed in all environments).
import importlib.util as _ilu, types as _types

if "src.backtest" not in sys.modules:
    _stub = _types.ModuleType("src.backtest")
    _stub.__path__ = [str(_root / "src" / "backtest")]
    _stub.__package__ = "src.backtest"
    sys.modules["src.backtest"] = _stub

from src.backtest.swing.paper_adapter import AdapterConfig, PaperAdapter
from src.execution.paper_broker import PaperBroker
from src.live.wal import WAL
from src.ops.kill_switch import KillSwitch


def _build_run_id(strategy: str | None = None, symbol: str | None = None) -> str:
    """Default run_id is stable per (strategy, symbol) so cron runs share the
    same WAL directory across 30-day operation. Position/balance state is
    restored via WAL replay on each cron startup (#143). Pass --run-id to
    override (e.g., for one-off smoke tests use --run-id smoke-$(date)).
    """
    if strategy and symbol:
        return f"phase1-{strategy}-{symbol}"
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Issue #175 - Swing strategy shadow run (paper trading)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--strategy",
        required=True,
        choices=["s2c-voltarget", "s4-funding", "r4-switch", "r6-switch"],
        help=(
            "Strategy variant. r4-switch (4h, Sharpe 1.218) / r6-switch (1h, "
            "Sharpe 1.201) — both #173 BEST tier. r6-switch = R4 logic with "
            "1h-tuned params (#199)."
        ),
    )
    parser.add_argument(
        "--interval",
        default=None,
        choices=["1h", "4h"],
        help=(
            "Bar interval. Default: 4h for r4-switch/s2c-voltarget/s4-funding, "
            "1h for r6-switch."
        ),
    )
    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
        help="Trading symbol (default BTCUSDT).",
    )
    parser.add_argument(
        "--exchange",
        default="binance-futures",
        choices=["binance-futures"],
        help="Exchange (default binance-futures).",
    )
    parser.add_argument(
        "--initial-balance",
        type=str,
        default="100000",
        help="Initial paper balance in USDT (default 100000).",
    )
    parser.add_argument(
        "--max-bars",
        type=int,
        default=None,
        help="Stop after N bars (test mode). Default: unlimited.",
    )
    parser.add_argument(
        "--history-bars",
        type=int,
        default=500,
        help="Number of historical 4h bars to fetch for signal warmup (default 500).",
    )
    parser.add_argument(
        "--log-dir",
        default="logs/shadow",
        help="Base log directory (default logs/shadow).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Run identifier (default: UTC timestamp).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    # s2c-voltarget params
    parser.add_argument("--entry-lookback", type=int, default=20)
    parser.add_argument("--exit-lookback", type=int, default=10)
    parser.add_argument("--vol-target", type=float, default=0.15)
    parser.add_argument("--vol-lookback", type=int, default=60)
    # s4-funding params
    parser.add_argument("--funding-threshold", type=float, default=-0.005e-2)
    # r4-switch params
    parser.add_argument(
        "--return-lookback",
        type=int,
        default=180,
        help="Rolling return lookback for r4-switch threshold regime (default 180 bars).",
    )

    return parser.parse_args(argv)


async def _fetch_candles(symbol: str, limit: int = 500, interval: str = "4h") -> "pd.DataFrame":
    """Fetch historical OHLCV from Binance Futures REST API.

    Returns DataFrame with columns: open, high, low, close, volume.
    Falls back to empty DataFrame if network unavailable (useful for dry-run/test).
    """
    import pandas as pd

    try:
        import aiohttp
    except ImportError:
        logger = logging.getLogger("shadow_run_swing")
        logger.warning("aiohttp not installed; returning empty candle DataFrame")
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "_funding_rate"])

    logger = logging.getLogger("shadow_run_swing")
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
        except Exception as exc:
            logger.warning("candle fetch failed: %s — using empty DataFrame", exc)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "_funding_rate"])

    rows = []
    for item in data:
        rows.append({
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
        })
    df = pd.DataFrame(rows)
    df["_funding_rate"] = 0.0  # placeholder; real funding fetched separately
    return df


async def run_shadow_swing(args: argparse.Namespace) -> int:
    """Main async entry point."""
    import pandas as pd
    from src.execution.mock_matching import MockMatchingEngine
    from src.execution.base import MarketState, Tick

    logger = logging.getLogger("shadow_run_swing")

    # Resolve interval / lookback defaults per strategy (#199 R6 1h variant).
    # r6-switch uses 1h bars with 4x bar counts to preserve same time horizons.
    # User-provided CLI args override these auto-resolved defaults.
    if args.strategy == "r6-switch":
        if args.interval is None:
            args.interval = "1h"
        # Bump lookbacks 4x only if still at 4h defaults (= user didn't override)
        if args.entry_lookback == 20:
            args.entry_lookback = 80
        if args.exit_lookback == 10:
            args.exit_lookback = 40
        if args.vol_lookback == 60:
            args.vol_lookback = 240
        if args.return_lookback == 180:
            args.return_lookback = 720
    else:
        if args.interval is None:
            args.interval = "4h"

    run_id = args.run_id or _build_run_id(args.strategy, args.symbol)
    wal_dir = Path(args.log_dir) / run_id
    wal_dir.mkdir(parents=True, exist_ok=True)
    wal_path = wal_dir / "wal.jsonl"

    logger.info(
        "shadow_run_swing start: strategy=%s symbol=%s interval=%s exchange=%s run_id=%s wal=%s",
        args.strategy, args.symbol, args.interval, args.exchange, run_id, wal_path,
    )

    kill_switch = KillSwitch()
    if wal_path.exists():
        broker = PaperBroker.from_wal(
            path=wal_path,
            kill_switch=kill_switch,
            matching_engine=MockMatchingEngine(),
            initial_balance=Decimal(args.initial_balance),
        )
        logger.info(
            "WAL replay: restored broker state from %s (positions=%d)",
            wal_path, len(await broker.get_positions()),
        )
    else:
        wal = WAL(wal_path)
        broker = PaperBroker(
            wal=wal,
            kill_switch=kill_switch,
            matching_engine=MockMatchingEngine(),
            initial_balance=Decimal(args.initial_balance),
        )
        logger.info("Fresh broker: WAL not found, starting from initial_balance=%s", args.initial_balance)

    config = AdapterConfig(
        strategy=args.strategy,
        symbol=args.symbol,
        initial_balance=Decimal(args.initial_balance),
        entry_lookback=args.entry_lookback,
        exit_lookback=args.exit_lookback,
        vol_target=args.vol_target,
        vol_lookback=args.vol_lookback,
        funding_threshold=args.funding_threshold,
        return_lookback=args.return_lookback,
    )
    adapter = PaperAdapter(config=config, broker=broker)

    # Restore PaperAdapter._in_position from broker positions (post-WAL-replay).
    # Without this, after a cron restart the adapter would think it's flat
    # while the broker still holds an open position → exit signals get lost.
    existing = await broker.get_positions(args.symbol)
    if existing:
        adapter._in_position = True
        adapter._entry_price = existing[0].entry_price
        logger.info(
            "Adapter state restored: in_position=True symbol=%s qty=%s entry=%s",
            args.symbol, existing[0].qty, existing[0].entry_price,
        )

    # Fetch historical candles for signal warmup
    df_hist = await _fetch_candles(args.symbol, limit=args.history_bars, interval=args.interval)
    if df_hist.empty:
        logger.warning("No historical candles; adapter will produce no signals until warmed up.")

    bar_count = 0
    max_bars = args.max_bars

    logger.info("Starting bar loop (max_bars=%s).", max_bars if max_bars else "unlimited")

    # In live operation this loop fires on each new 4h bar close.
    # For the skeleton/test mode we iterate over existing history bars.
    for i in range(1, len(df_hist) + 1):
        if max_bars is not None and bar_count >= max_bars:
            break

        df_slice = df_hist.iloc[:i].copy()
        current_close = df_slice["close"].iloc[-1]

        # Update paper broker market state for matching
        tick = Tick(
            symbol=args.symbol,
            bid=current_close * 0.9999,
            ask=current_close * 1.0001,
            last=current_close,
            volume=0,
            ts=datetime.now(timezone.utc),
        )
        broker.update_market(MarketState(tick=tick))

        ack = await adapter.on_bar(df_slice)
        if ack is not None:
            logger.info("bar %d: order ack status=%s id=%s", i, ack.status, ack.client_order_id)

        bar_count += 1

    logger.info(
        "shadow_run_swing complete: bars_processed=%d wal=%s",
        bar_count, wal_path,
    )
    await broker.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        return asyncio.run(run_shadow_swing(args))
    except KeyboardInterrupt:
        logging.getLogger("shadow_run_swing").info("Interrupted by user")
        return 130
    except Exception as exc:
        logging.getLogger("shadow_run_swing").exception("shadow_run_swing failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
