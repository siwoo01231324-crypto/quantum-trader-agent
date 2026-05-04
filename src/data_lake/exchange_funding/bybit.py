"""Bybit funding rate fetcher.

Endpoint: GET https://api.bybit.com/v5/market/funding/history
    ?category=linear&symbol=BTCUSDT&startTime=<ms>&endTime=<ms>&limit=200

- Max 200 records per request (Bybit hard limit for this endpoint).
- Pagination: advance startTime past last record's fundingRateTimestamp.
- Rate limit: 120 req/min public tier. Sleep 0.5s between pages to stay safe.
- Funding interval: 8h (same schedule as Binance), Bybit uses "linear" category for USDT perps.
- 5y BTC history ≈ 5475 rows → ~28 requests.

Usage (direct):
    from src.data_lake.exchange_funding.bybit import fetch_funding_history
    df = fetch_funding_history("BTCUSDT", "2020-09-01", "2025-12-31")

Output DataFrame columns: [ts (UTC DatetimeTZ), funding_rate (float64)]
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import pandas as pd
import requests

log = logging.getLogger(__name__)

_BASE_URL = "https://api.bybit.com/v5/market/funding/history"
_LIMIT = 200
_SLEEP_BETWEEN = 0.5
_MAX_RETRIES = 3
_RETRY_BASE = 1.0


def _get_with_retry(url: str, params: dict) -> dict:
    """GET with exponential backoff on 429/503."""
    delay = _RETRY_BASE
    for attempt in range(_MAX_RETRIES + 1):
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (429, 503):
            if attempt < _MAX_RETRIES:
                log.warning("HTTP %d, retrying in %.1fs", resp.status_code, delay)
                time.sleep(delay)
                delay *= 2
                continue
            resp.raise_for_status()
        resp.raise_for_status()
    return {}  # unreachable


def fetch_funding_history(
    symbol: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    """Fetch Bybit USDT-M funding rate history with pagination.

    Parameters
    ----------
    symbol : Bybit linear symbol, e.g. "BTCUSDT"
    start  : ISO date string e.g. "2020-09-01"
    end    : ISO date string e.g. "2025-12-31"

    Returns
    -------
    pd.DataFrame with columns [ts, funding_rate] sorted ascending.
    ts is UTC-aware Timestamp, funding_rate is float64.
    """
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    all_records: list[dict] = []
    current_start_ms = start_ms

    while True:
        params = {
            "category": "linear",
            "symbol": symbol,
            "startTime": current_start_ms,
            "endTime": end_ms,
            "limit": _LIMIT,
        }
        raw = _get_with_retry(_BASE_URL, params)

        # Bybit response: {"retCode": 0, "result": {"list": [...]}}
        if raw.get("retCode") != 0:
            log.error("Bybit API error: %s", raw.get("retMsg", raw))
            break

        data = raw.get("result", {}).get("list", [])
        if not data:
            break

        for row in data:
            ts_ms = int(row["fundingRateTimestamp"])
            ts = pd.Timestamp(
                datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            )
            all_records.append({
                "ts": ts,
                "funding_rate": float(row["fundingRate"]),
            })

        if len(data) < _LIMIT:
            break

        # Advance past the last record
        last_ts_ms = int(data[-1]["fundingRateTimestamp"])
        current_start_ms = last_ts_ms + 1

        if current_start_ms >= end_ms:
            break

        time.sleep(_SLEEP_BETWEEN)

    if not all_records:
        return pd.DataFrame(columns=["ts", "funding_rate"])

    df = pd.DataFrame(all_records)
    df = df.sort_values("ts").reset_index(drop=True)
    return df
