#!/usr/bin/env python3
"""Fetch Binance USDT-M perp top-10 alt 5m OHLCV (user-run, 60-120 min).

Issue #185 Phase D gate: user must run this before D6~D9 variants.

Usage:
    python scripts/fetch_alt_universe_ohlcv.py \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,DOGEUSDT,LINKUSDT,ATOMUSDT \\
        --freq 5m --start 2020-01-01 --end 2025-12-31 \\
        --out lake/ohlcv/freq=5m

    python scripts/fetch_alt_universe_ohlcv.py --dry-run   # print plan only, no API calls

Output layout (Hive partition, same convention as 1m lake):
    lake/ohlcv/freq=5m/year=YYYY/month=MM/symbol=XXX/part-0.parquet

Rate limit: Binance Futures allows 1200 weight/min. Each klines request costs 2 weight.
With 1000 candles/req and 5m interval, 5 years = ~525,000 bars = 525 requests per symbol.
10 symbols → 5,250 requests total, ~5-10 min/symbol at 0.5 s inter-request sleep.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.data_lake.fetcher import fetch_binance_futures_klines, save_ohlcv_parquet  # noqa: E402

log = logging.getLogger("fetch_alt_universe_ohlcv")

_DEFAULT_SYMBOLS = (
    "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,"
    "ADAUSDT,AVAXUSDT,DOGEUSDT,LINKUSDT,ATOMUSDT"
)
_INTER_SYMBOL_SLEEP = 1.0  # seconds between symbols (Binance courtesy gap)

# Approximate disk size per symbol per year for 5m OHLCV (rough estimate)
_APPROX_MB_PER_SYMBOL_PER_YEAR = 8.5  # ~8.5 MB per symbol per year at 5m


def _estimate_plan(symbols: list[str], start: str, end: str, out_dir: Path) -> None:
    """Print fetch plan without making any API calls."""
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    years = (end_ts - start_ts).days / 365.25
    n_bars_per_sym = int(years * 365.25 * 24 * 12)  # 5m bars per year: 105,120

    # Requests per symbol: ceil(n_bars / 1000)
    reqs_per_sym = (n_bars_per_sym + 999) // 1000
    total_reqs = reqs_per_sym * len(symbols)
    est_secs = total_reqs * _INTER_SYMBOL_SLEEP + len(symbols) * _INTER_SYMBOL_SLEEP
    est_min = est_secs / 60
    est_disk_gb = len(symbols) * years * _APPROX_MB_PER_SYMBOL_PER_YEAR / 1024

    print("=" * 60)
    print("DRY-RUN — fetch plan (no API calls made)")
    print("=" * 60)
    print(f"  Symbols  : {', '.join(symbols)}")
    print(f"  Interval : 5m")
    print(f"  Window   : {start} → {end}  ({years:.1f} yr)")
    print(f"  Bars/sym : ~{n_bars_per_sym:,}")
    print(f"  Reqs/sym : ~{reqs_per_sym:,}")
    print(f"  Total req: ~{total_reqs:,}")
    print(f"  Est time : ~{est_min:.0f} min")
    print(f"  Est disk : ~{est_disk_gb:.1f} GB")
    print(f"  Out root : {out_dir.resolve()}")
    print()
    print("Partition paths (one per symbol per year/month):")
    for sym in symbols[:2]:
        print(f"  {out_dir}/year=2020/month=01/symbol={sym}/part-0.parquet")
        print(f"  {out_dir}/year=2020/month=02/symbol={sym}/part-0.parquet")
        print(f"  ... (total ~{int(years * 12)} files per symbol)")
    if len(symbols) > 2:
        print(f"  ... and {len(symbols) - 2} more symbols")
    print("=" * 60)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--symbols",
        default=_DEFAULT_SYMBOLS,
        help="Comma-separated Binance USDT-M perp symbols",
    )
    p.add_argument("--freq", default="5m", help="Candle interval (default: 5m)")
    p.add_argument("--start", default="2020-01-01", help="Start date ISO")
    p.add_argument("--end", default="2025-12-31", help="End date ISO")
    p.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "lake",
        help=(
            "Lake root directory. Parquet files are written to "
            "<out>/ohlcv/freq=<freq>/year=YYYY/month=MM/symbol=XXX/part-0.parquet. "
            "When --out ends in 'freq=5m' the output goes directly there; "
            "otherwise the standard ohlcv/freq=<freq> sub-path is appended."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print fetch plan (API call count, disk estimate) without fetching.",
    )
    p.add_argument(
        "--inter-symbol-sleep",
        type=float,
        default=_INTER_SYMBOL_SLEEP,
        help="Seconds to sleep between symbols (default: 1.0)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args(argv)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        log.error("No symbols specified.")
        return 1

    # Determine lake root: if --out already ends with freq= pattern, use parent
    out_path = args.out.resolve()
    if out_path.name.startswith("freq="):
        lake_root = out_path.parent.parent  # strip ohlcv/freq=xxx
    else:
        lake_root = out_path

    if args.dry_run:
        _estimate_plan(symbols, args.start, args.end, lake_root / "ohlcv" / f"freq={args.freq}")
        return 0

    total = len(symbols)
    for idx, symbol in enumerate(symbols, start=1):
        if idx > 1:
            log.info("Sleeping %.1fs between symbols...", args.inter_symbol_sleep)
            time.sleep(args.inter_symbol_sleep)

        log.info("[%d/%d] Fetching %s %s %s→%s", idx, total, symbol, args.freq, args.start, args.end)
        try:
            df = fetch_binance_futures_klines(
                symbol=symbol,
                interval=args.freq,
                start=args.start,
                end=args.end,
            )
        except Exception as exc:
            log.error("Failed to fetch %s: %s", symbol, exc)
            continue

        if df.empty:
            log.warning("No data returned for %s — skipping.", symbol)
            continue

        log.info("  Fetched %d candles. Saving...", len(df))
        try:
            saved = save_ohlcv_parquet(df, lake_root, symbol=symbol, freq=args.freq)
            for p in saved:
                log.info("    Wrote: %s", p)
        except Exception as exc:
            log.error("Failed to save %s: %s", symbol, exc)
            continue

    log.info("Done. %d symbols processed.", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
