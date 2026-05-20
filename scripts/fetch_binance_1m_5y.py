"""Binance 1-minute kline 5y fetch + parquet cache (#227 follow-up).

Fetches the last 5 years of 1m candles for Binance USDT-perp top-30 symbols
(same universe as bench_cs_tsmom_crypto), caches each symbol as parquet
under ``data/cache/binance_1m/<symbol>.parquet`` so bench_live_scanner.py
can run a per-minute simulation.

Usage::

    # Default — top-30 USDT pairs, last 5y
    python scripts/fetch_binance_1m_5y.py

    # Subset / shorter horizon
    python scripts/fetch_binance_1m_5y.py --symbols BTCUSDT,ETHUSDT --years 1
    python scripts/fetch_binance_1m_5y.py --refresh   # re-fetch even if cached

Network: Binance public endpoint ``api.binance.com/api/v3/klines`` — no
authentication. Rate limit: ~1200 req/min weight 1 per request → 0.05s
sleep between calls is sufficient. 5y of 1m bars per symbol = 2,628,000
candles → 2628 calls of 1000-candle pages → ~2.5 minutes per symbol over
the wire. Total budget ~80 min for 30 symbols on a quiet network.

Output schema per parquet:
    Index: pd.DatetimeIndex (UTC)
    Columns: open, high, low, close, volume, quote_volume

Disk: each parquet ~25-40 MB (compressed). 30 symbols ~1 GB total.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
CACHE_DIR = ROOT / "data" / "cache" / "binance_1m"

_BINANCE_API = "https://api.binance.com/api/v3/klines"
_PAGE_SIZE = 1000  # max kline limit per request
_INTERVAL = "1m"


def _fetch_page(client: httpx.Client, symbol: str, start_ms: int, end_ms: int) -> list[list]:
    """Fetch one 1000-candle page from Binance public klines API."""
    resp = client.get(
        _BINANCE_API,
        params={
            "symbol": symbol,
            "interval": _INTERVAL,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": _PAGE_SIZE,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_symbol_klines(symbol: str, start_ts: pd.Timestamp, end_ts: pd.Timestamp,
                        sleep_sec: float = 0.05) -> pd.DataFrame:
    """Iterate through pages until end_ts is reached.

    Binance returns up to 1000 candles per call. We page forward by setting
    next ``startTime = last_candle_close_ms + 1`` until we exit the window
    or the page returns empty (symbol delisted before end_ts).
    """
    cur = int(start_ts.timestamp() * 1000)
    end_ms = int(end_ts.timestamp() * 1000)
    rows: list[list] = []
    with httpx.Client() as client:
        while cur < end_ms:
            page = _fetch_page(client, symbol, cur, end_ms)
            if not page:
                break
            rows.extend(page)
            last_close_ms = page[-1][6]  # close_time field
            if last_close_ms <= cur:
                break  # no progress — guard against infinite loop
            cur = last_close_ms + 1
            time.sleep(sleep_sec)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("ts")[["open", "high", "low", "close", "volume", "quote_volume"]]
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _resolve_symbols(args) -> list[str]:
    if args.symbols:
        return [s.strip() for s in args.symbols.split(",") if s.strip()]
    # Re-use bench_cs_tsmom_crypto's universe builder so we cache the same
    # symbols the bench harness will read.
    bench_bn = __import__("bench_cs_tsmom_crypto")
    return bench_bn.fetch_top_universe(args.universe_size)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fetch_binance_1m_5y")
    parser.add_argument("--symbols", default=None,
                        help="comma-separated symbols (default: top-30 USDT pairs)")
    parser.add_argument("--universe-size", type=int, default=30)
    parser.add_argument("--years", type=float, default=5.0)
    parser.add_argument("--refresh", action="store_true",
                        help="bypass parquet cache; refetch from API")
    parser.add_argument("--sleep-sec", type=float, default=0.05,
                        help="rate-limit cushion between paginated requests")
    args = parser.parse_args(argv)

    # Windows console / file-redirect uses the locale codec (cp949 on a
    # Korean box). The top-N universe can include a non-ASCII junk ticker
    # (observed: 币安人生USDT) → print() raised UnicodeEncodeError and killed
    # the WHOLE 30-symbol job at symbol 25. Make stdout/stderr lossy-UTF-8
    # so one odd ticker name never aborts the fetch.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    symbols = _resolve_symbols(args)
    print(f"[fetch_binance_1m] {len(symbols)} symbols, {args.years}y horizon",
          flush=True)

    end_ts = pd.Timestamp.utcnow().normalize()
    start_ts = end_ts - pd.Timedelta(days=int(args.years * 365.25))

    summary: list[dict] = []
    for i, symbol in enumerate(symbols, 1):
        cache_path = CACHE_DIR / f"{symbol}.parquet"
        if cache_path.exists() and not args.refresh:
            try:
                cached = pd.read_parquet(cache_path)
                summary.append({"symbol": symbol, "rows": len(cached), "from": "cache"})
                print(f"  [{i}/{len(symbols)}] {symbol}: {len(cached)} rows (cached)",
                      flush=True)
                continue
            except Exception:
                pass
        t0 = time.monotonic()
        try:
            df = fetch_symbol_klines(symbol, start_ts, end_ts,
                                     sleep_sec=args.sleep_sec)
        except Exception as exc:
            print(f"  [{i}/{len(symbols)}] {symbol}: FETCH FAILED — {exc}",
                  flush=True)
            summary.append({"symbol": symbol, "error": str(exc)})
            continue
        if df.empty:
            print(f"  [{i}/{len(symbols)}] {symbol}: empty response",
                  flush=True)
            summary.append({"symbol": symbol, "rows": 0})
            continue
        df.to_parquet(cache_path)
        elapsed = time.monotonic() - t0
        print(f"  [{i}/{len(symbols)}] {symbol}: {len(df)} rows ({elapsed:.1f}s)",
              flush=True)
        summary.append({"symbol": symbol, "rows": len(df),
                        "elapsed_sec": round(elapsed, 1)})

    summary_path = CACHE_DIR / "_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"[fetch_binance_1m] done — summary at {summary_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
