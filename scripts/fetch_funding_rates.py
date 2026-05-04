#!/usr/bin/env python3
"""Fetch historical funding rate data from Binance, OKX, and Bybit.

Binance USDT-M perpetual funding rate endpoint:
    GET https://fapi.binance.com/fapi/v1/fundingRate
    ?symbol=BTCUSDT&startTime=<ms>&endTime=<ms>&limit=1000

Rate: every 8h (00:00, 08:00, 16:00 UTC). ~1095 rows/year.
5 years (2020-09 to 2025-12) ~ 5475 rows ~ 6 requests.

Usage:
    # Binance only (default, backward-compatible)
    python scripts/fetch_funding_rates.py \
        --symbols BTCUSDT \
        --start 2020-09-01 --end 2025-12-31 \
        --output-dir lake/

    # OKX (symbol format: BTC-USDT-SWAP)
    python scripts/fetch_funding_rates.py \
        --exchange okx \
        --symbols BTC-USDT-SWAP \
        --start 2020-09-01 --end 2025-12-31 \
        --output-dir lake/

    # Bybit
    python scripts/fetch_funding_rates.py \
        --exchange bybit \
        --symbols BTCUSDT \
        --start 2020-09-01 --end 2025-12-31 \
        --output-dir lake/

    # All three exchanges at once
    python scripts/fetch_funding_rates.py \
        --exchange binance,okx,bybit \
        --symbols BTCUSDT \
        --start 2020-09-01 --end 2025-12-31 \
        --output-dir lake/

Output (exchange-partitioned, issue #174):
    lake/funding_rate/exchange=binance/symbol=BTCUSDT/part-0.parquet
    lake/funding_rate/exchange=okx/symbol=BTC-USDT-SWAP/part-0.parquet
    lake/funding_rate/exchange=bybit/symbol=BTCUSDT/part-0.parquet

Legacy output (binance-only, backward-compatible):
    lake/funding_rate/symbol=BTCUSDT/part-0.parquet  (still written when --exchange binance)
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

log = logging.getLogger(__name__)

BINANCE_FUNDING_RATE_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
_LIMIT = 1000  # max rows per request
_SLEEP_BETWEEN = 0.5  # seconds between paginated requests
_MAX_RETRIES = 3
_RETRY_BASE = 1.0


def _get_with_retry(url: str, params: dict) -> list:
    """GET with exponential backoff on 429."""
    delay = _RETRY_BASE
    for attempt in range(_MAX_RETRIES + 1):
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            if attempt < _MAX_RETRIES:
                log.warning("429 rate limit, retrying in %.1fs", delay)
                time.sleep(delay)
                delay *= 2
                continue
            resp.raise_for_status()
        resp.raise_for_status()
    return []  # unreachable


def fetch_funding_rates(
    symbol: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    """Fetch Binance Futures funding rate history with pagination.

    Parameters
    ----------
    symbol : e.g. "BTCUSDT"
    start  : ISO date string e.g. "2020-09-01"
    end    : ISO date string e.g. "2025-12-31"

    Returns
    -------
    pd.DataFrame with columns [symbol, ts, funding_rate, mark_price].
    """
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    all_records: list[dict] = []
    current_start_ms = start_ms

    while True:
        params = {
            "symbol": symbol,
            "startTime": current_start_ms,
            "endTime": end_ms,
            "limit": _LIMIT,
        }
        raw = _get_with_retry(BINANCE_FUNDING_RATE_URL, params)
        if not raw:
            break

        for row in raw:
            ts = pd.Timestamp(
                datetime.fromtimestamp(int(row["fundingTime"]) / 1000.0, tz=timezone.utc)
            )
            all_records.append({
                "symbol": row["symbol"],
                "ts": ts,
                "funding_rate": float(row["fundingRate"]),
                "mark_price": float(row.get("markPrice") or 0.0),
            })

        if len(raw) < _LIMIT:
            break

        # Advance past the last record
        last_time_ms = int(raw[-1]["fundingTime"])
        current_start_ms = last_time_ms + 1

        if current_start_ms >= end_ms:
            break

        time.sleep(_SLEEP_BETWEEN)

    if not all_records:
        return pd.DataFrame(columns=["symbol", "ts", "funding_rate", "mark_price"])

    df = pd.DataFrame(all_records)
    df = df.sort_values("ts").reset_index(drop=True)
    return df


def save_funding_parquet(
    df: pd.DataFrame,
    output_dir: Path,
    symbol: str,
) -> Path:
    """Save funding rate DataFrame to a single parquet file.

    Path: output_dir/funding_rate/symbol={symbol}/part-0.parquet
    """
    part_dir = output_dir / "funding_rate" / f"symbol={symbol}"
    part_dir.mkdir(parents=True, exist_ok=True)
    out_path = part_dir / "part-0.parquet"

    table = pa.Table.from_pandas(df.reset_index(drop=True))
    pq.write_table(table, out_path)
    return out_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch funding rate history from Binance, OKX, and/or Bybit.",
    )
    parser.add_argument(
        "--symbols", default="BTCUSDT",
        help="Comma-separated trading pairs (default: BTCUSDT). "
             "Use exchange-native format: BTCUSDT for Binance/Bybit, BTC-USDT-SWAP for OKX.",
    )
    parser.add_argument(
        "--exchange", default="binance",
        help="Comma-separated exchanges to fetch from: binance, okx, bybit "
             "(default: binance). Example: --exchange binance,okx,bybit",
    )
    parser.add_argument(
        "--start", default="2020-09-01",
        help="Start date ISO (default: 2020-09-01, perpetual launch)",
    )
    parser.add_argument(
        "--end", default="2025-12-31",
        help="End date ISO (default: 2025-12-31)",
    )
    parser.add_argument(
        "--output-dir", default="lake/",
        help="Output directory (default: lake/)",
    )
    return parser


def _save_exchange_parquet(
    df: pd.DataFrame,
    output_dir: Path,
    exchange: str,
    symbol: str,
) -> Path:
    """Save to exchange-partitioned path: output_dir/funding_rate/exchange={exchange}/symbol={symbol}/part-0.parquet."""
    part_dir = output_dir / "funding_rate" / f"exchange={exchange}" / f"symbol={symbol}"
    part_dir.mkdir(parents=True, exist_ok=True)
    out_path = part_dir / "part-0.parquet"
    table = pa.Table.from_pandas(df.reset_index(drop=True))
    pq.write_table(table, out_path)
    return out_path


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    exchanges = [e.strip().lower() for e in args.exchange.split(",") if e.strip()]

    _SUPPORTED = {"binance", "okx", "bybit"}
    unknown = set(exchanges) - _SUPPORTED
    if unknown:
        parser.error(f"Unknown exchange(s): {unknown}. Supported: {_SUPPORTED}")

    for exchange in exchanges:
        for idx, symbol in enumerate(symbols):
            if idx > 0:
                time.sleep(_SLEEP_BETWEEN)

            print(
                f"[{exchange}] Fetching funding rates for {symbol} "
                f"from {args.start} to {args.end} -> {output_dir}"
            )

            if exchange == "binance":
                df = fetch_funding_rates(symbol=symbol, start=args.start, end=args.end)
                # Keep legacy path for binance backward-compat
                if not df.empty:
                    legacy_path = save_funding_parquet(df, output_dir, symbol=symbol)
                    print(f"  Wrote (legacy): {legacy_path}")
            elif exchange == "okx":
                from src.data_lake.exchange_funding.okx import fetch_funding_history
                df = fetch_funding_history(symbol=symbol, start=args.start, end=args.end)
            elif exchange == "bybit":
                from src.data_lake.exchange_funding.bybit import fetch_funding_history
                df = fetch_funding_history(symbol=symbol, start=args.start, end=args.end)
            else:
                continue  # guarded above

            if df.empty:
                print(f"  No data fetched for {symbol} on {exchange}.")
                continue

            print(f"  Fetched {len(df)} funding rate records.")
            out_path = _save_exchange_parquet(df, output_dir, exchange=exchange, symbol=symbol)
            print(f"  Wrote: {out_path}")

    print("Done.")


if __name__ == "__main__":
    main()
