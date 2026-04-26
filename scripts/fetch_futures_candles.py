#!/usr/bin/env python3
"""Fetch historical Binance Futures USDT-M candle data (multi-symbol).

Used by #80 Phase E ``shadow_report.py --compare-backtest`` (data_source =
``"binance_futures_usdtm"``). Default symbol set BTCUSDT/ETHUSDT/SOLUSDT
matches the Phase 1 Shadow Paper acceptance criteria (#106 AC2).

Usage:
    python scripts/fetch_futures_candles.py \
        --symbols BTCUSDT,ETHUSDT,SOLUSDT \
        --interval 1m \
        --start 2026-04-01 --end 2026-04-26 \
        --output-dir lake/

Defaults:
    --symbols     BTCUSDT,ETHUSDT,SOLUSDT
    --interval    1m
    --start       1 year ago from today (UTC)
    --end         today (UTC)
    --output-dir  lake/
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from data_lake.fetcher import fetch_binance_futures_klines, save_ohlcv_parquet

_DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT"
_INTER_SYMBOL_SLEEP = 0.5  # rate-limit courtesy gap between fan-out symbols


def build_parser() -> argparse.ArgumentParser:
    now_utc = datetime.now(tz=timezone.utc)
    default_end = now_utc.strftime("%Y-%m-%d")
    default_start = (now_utc - timedelta(days=365)).strftime("%Y-%m-%d")

    parser = argparse.ArgumentParser(
        description="Fetch historical Binance Futures USDT-M OHLCV candles (multi-symbol).",
    )
    parser.add_argument(
        "--symbols", default=_DEFAULT_SYMBOLS,
        help=f"Comma-separated trading pairs (default: {_DEFAULT_SYMBOLS})",
    )
    parser.add_argument(
        "--interval", default="1m",
        help="Candle interval (default: 1m)",
    )
    parser.add_argument(
        "--start", default=default_start,
        help=f"Start date ISO (default: {default_start})",
    )
    parser.add_argument(
        "--end", default=default_end,
        help=f"End date ISO (default: {default_end})",
    )
    parser.add_argument(
        "--output-dir", default="lake/",
        help="Output directory (default: lake/)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    for idx, symbol in enumerate(symbols):
        if idx > 0:
            time.sleep(_INTER_SYMBOL_SLEEP)

        print(
            f"Fetching {symbol} {args.interval} from {args.start} to {args.end} "
            f"-> {output_dir}",
        )
        df = fetch_binance_futures_klines(
            symbol=symbol,
            interval=args.interval,
            start=args.start,
            end=args.end,
        )
        if df.empty:
            print(f"  No data fetched for {symbol}.")
            continue

        print(f"  Fetched {len(df)} candles. Saving to parquet...")
        saved = save_ohlcv_parquet(df, output_dir, symbol=symbol, freq=args.interval)
        for p in saved:
            print(f"    Wrote: {p}")

    print("Done.")


if __name__ == "__main__":
    main()
