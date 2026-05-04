#!/usr/bin/env python3
"""Shadow Run - Swing Strategy Paper Trading CLI (Issue #175).

Runs s2c-voltarget or s4-funding strategy against live 4h Binance Futures
candles, submitting signals to PaperBroker and recording all fills to WAL.

Usage:
    python scripts/shadow_run_swing.py --strategy s2c-voltarget --symbol BTCUSDT
    python scripts/shadow_run_swing.py --strategy s4-funding --symbol BTCUSDT --exchange binance-futures
    python scripts/shadow_run_swing.py --strategy s2c-voltarget --symbol BTCUSDT --max-bars 3

WAL location: logs/shadow/{run_id}/wal.jsonl

Strategy variants (shard-registered, see 01_plan.md):
    s2c-voltarget : s2_donchian_voltarget  (entry=20, exit=10, vol_target=0.15)
    s4-funding    : s4_funding_carry        (threshold=-0.005%)

Cron example (every 4h at bar close):
    0 1,5,9,13,17,21 * * * python scripts/shadow_run_swing.py --strategy s2c-voltarget --symbol BTCUSDT --max-bars 1
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


def _build_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Issue #175 - Swing strategy shadow run (paper trading)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--strategy",
        required=True,
        choices=["s2c-voltarget", "s4-funding"],
        help="Strategy variant to run.",
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

    return parser.parse_args(argv)


async def _fetch_candles(symbol: str, limit: int = 500) -> "pd.DataFrame":
    """Fetch historical 4h OHLCV from Binance Futures REST API.

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
    params = {"symbol": symbol, "interval": "4h", "limit": limit}

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

    run_id = args.run_id or _build_run_id()
    wal_dir = Path(args.log_dir) / run_id
    wal_dir.mkdir(parents=True, exist_ok=True)
    wal_path = wal_dir / "wal.jsonl"

    logger.info(
        "shadow_run_swing start: strategy=%s symbol=%s exchange=%s run_id=%s wal=%s",
        args.strategy, args.symbol, args.exchange, run_id, wal_path,
    )

    wal = WAL(wal_path)
    kill_switch = KillSwitch()
    broker = PaperBroker(
        wal=wal,
        kill_switch=kill_switch,
        matching_engine=MockMatchingEngine(),
        initial_balance=Decimal(args.initial_balance),
    )

    config = AdapterConfig(
        strategy=args.strategy,
        symbol=args.symbol,
        initial_balance=Decimal(args.initial_balance),
        entry_lookback=args.entry_lookback,
        exit_lookback=args.exit_lookback,
        vol_target=args.vol_target,
        vol_lookback=args.vol_lookback,
        funding_threshold=args.funding_threshold,
    )
    adapter = PaperAdapter(config=config, broker=broker)

    # Fetch historical candles for signal warmup
    df_hist = await _fetch_candles(args.symbol, limit=args.history_bars)
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
