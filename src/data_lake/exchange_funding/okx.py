"""OKX funding rate fetcher.

Endpoint: GET https://www.okx.com/api/v5/public/funding-rate-history
    ?instId=BTC-USDT-SWAP&after=<ms>&limit=100

- Max 100 records per request (OKX hard limit).
- Pagination: OKX `after=T` returns records with fundingTime < T (OLDER than T).
  Walk backward: start with no cursor (most recent page), advance `after` to the
  earliest ts in each page.
- OKX public API retains ~3 months of history only. Requests for older dates
  will return empty results — this is an API limitation, not a code error.
- Rate limit: ~20 req/2s public tier. Sleep 0.12s between pages to stay safe.
- Funding interval: 8h (00:00, 08:00, 16:00 UTC), same as Binance.

Usage (direct):
    from src.data_lake.exchange_funding.okx import fetch_funding_history
    df = fetch_funding_history("BTC-USDT-SWAP", "2020-09-01", "2025-12-31")

Output DataFrame columns: [ts (UTC DatetimeTZ), funding_rate (float64)]
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import pandas as pd
import requests

log = logging.getLogger(__name__)

_BASE_URL = "https://www.okx.com/api/v5/public/funding-rate-history"
_LIMIT = 100
_SLEEP_BETWEEN = 0.12
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
    """Fetch OKX funding rate history with pagination.

    Parameters
    ----------
    symbol : OKX instrument ID, e.g. "BTC-USDT-SWAP"
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
    # OKX API semantics (verified empirically):
    #   after=T  → returns records with fundingTime < T  (OLDER than T, walks backward)
    #   before=T → returns records with fundingTime > T  (NEWER than T)
    # Strategy: start with no cursor (gets most recent page), skip records > end_ms,
    # collect records in [start_ms, end_ms], stop when we reach start_ms.
    # Advance cursor using `after` = earliest ts in each page.
    cursor_ms: int | None = None  # no cursor on first request = most recent page

    while True:
        params: dict = {
            "instId": symbol,
            "limit": _LIMIT,
        }
        if cursor_ms is not None:
            params["after"] = str(cursor_ms)

        raw = _get_with_retry(_BASE_URL, params)

        # OKX response: {"code": "0", "data": [...]}
        if raw.get("code") != "0":
            log.error("OKX API error: %s", raw.get("msg", raw))
            break

        data = raw.get("data", [])
        if not data:
            break

        reached_start = False
        for row in data:
            ts_ms = int(row["fundingTime"])
            if ts_ms > end_ms:
                # Skip records newer than our requested end date
                continue
            if ts_ms < start_ms:
                reached_start = True
                break
            ts = pd.Timestamp(
                datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
            )
            all_records.append({
                "ts": ts,
                "funding_rate": float(row["fundingRate"]),
            })

        if reached_start:
            break

        if len(data) < _LIMIT:
            break

        # Advance cursor to the earliest fundingTime in this batch (walk backward)
        earliest_ms = int(data[-1]["fundingTime"])
        if earliest_ms <= start_ms:
            break
        cursor_ms = earliest_ms

        time.sleep(_SLEEP_BETWEEN)

    if not all_records:
        return pd.DataFrame(columns=["ts", "funding_rate"])

    df = pd.DataFrame(all_records)
    df = df.sort_values("ts").reset_index(drop=True)
    return df
