#!/usr/bin/env python3
"""Fetch historical candle data from Binance REST API.

Usage:
    python scripts/fetch_candles.py --symbol BTCUSDT --interval 15m \
        --start 2025-04-01 --end 2026-04-01 --output-dir lake/

Defaults:
    --start   1 year ago from today
    --end     today (UTC)
    --output-dir  lake/
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from data_lake.fetcher import fetch_binance_klines, save_ohlcv_parquet


def build_parser() -> argparse.ArgumentParser:
    now_utc = datetime.now(tz=timezone.utc)
    default_end = now_utc.strftime("%Y-%m-%d")
    default_start = (now_utc - timedelta(days=365)).strftime("%Y-%m-%d")

    parser = argparse.ArgumentParser(
        description="Fetch historical OHLCV candle data from Binance REST API."
    )
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading pair (default: BTCUSDT)")
    parser.add_argument("--interval", default="15m", help="Candle interval (default: 15m)")
    parser.add_argument("--start", default=default_start, help=f"Start date ISO (default: {default_start})")
    parser.add_argument("--end", default=default_end, help=f"End date ISO (default: {default_end})")
    parser.add_argument("--output-dir", default="lake/", help="Output directory (default: lake/)")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    print(f"Fetching {args.symbol} {args.interval} from {args.start} to {args.end} -> {output_dir}")

    df = fetch_binance_klines(
        symbol=args.symbol,
        interval=args.interval,
        start=args.start,
        end=args.end,
    )

    if df.empty:
        print("No data fetched.")
        return

    print(f"Fetched {len(df)} candles. Saving to parquet...")
    saved = save_ohlcv_parquet(df, output_dir, symbol=args.symbol, freq=args.interval)
    for p in saved:
        print(f"  Wrote: {p}")
    print("Done.")


if __name__ == "__main__":
    main()
