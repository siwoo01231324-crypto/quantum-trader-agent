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

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))           # for "from src.xxx" imports inside data_lake.fetcher
sys.path.insert(0, str(_ROOT / "src"))   # for top-level "data_lake.xxx" import below

from data_lake.fetcher import (
    fetch_binance_klines,
    fetch_binance_vision_klines,
    save_ohlcv_parquet,
)


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
    parser.add_argument(
        "--source",
        choices=["binance-api", "binance-vision"],
        default="binance-api",
        help="Data source. Use 'binance-vision' (S3 dump) when api.binance.com is geo-blocked (e.g. GitHub-hosted runners return 451).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    print(f"Fetching {args.symbol} {args.interval} from {args.start} to {args.end} -> {output_dir} (source={args.source})")

    fetch_fn = fetch_binance_vision_klines if args.source == "binance-vision" else fetch_binance_klines
    df = fetch_fn(
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
