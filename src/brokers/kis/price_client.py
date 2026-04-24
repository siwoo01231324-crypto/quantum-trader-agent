"""KIS daily OHLCV price client (raw TR layer).

Fetches daily bar data via KIS inquiry TR FHKST03010100
(`/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice`).

Rate limit: paper 2 req/s → sleep 0.5s between pages.
429 retry: self-implemented (KISClient._request_with_retry handles 5xx only).
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import requests

from src.brokers.kis.schemas import KISDailyBar
from src.brokers.kis.tr_ids import TR_ID_DAILY_PRICE

if TYPE_CHECKING:
    from src.brokers.kis.rest import KISClient

log = logging.getLogger(__name__)

_RATE_LIMIT_SLEEP = 0.5   # seconds between paginated requests (paper 2 rps)
_429_MAX_RETRIES = 3
_429_BASE_DELAY = 1.0      # seconds, doubles each attempt

_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"


def _call_with_429_retry(client: "KISClient", params: dict) -> dict:
    """Wrap client._get with 429-aware retry + Retry-After header support."""
    delay = _429_BASE_DELAY
    for attempt in range(_429_MAX_RETRIES):
        try:
            return client._get(_PATH, TR_ID_DAILY_PRICE, params)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status == 429:
                if attempt < _429_MAX_RETRIES - 1:
                    retry_after = exc.response.headers.get("Retry-After") if exc.response is not None else None
                    wait = float(retry_after) if retry_after else delay
                    log.warning(
                        "KIS 429 rate-limit (attempt %d/%d), waiting %.1fs",
                        attempt + 1, _429_MAX_RETRIES, wait,
                    )
                    time.sleep(wait)
                    delay *= 2
                    continue
                raise
            raise
    # unreachable — loop always returns or raises
    raise RuntimeError("unexpected exit from retry loop")


def fetch_daily_ohlcv_raw(
    client: "KISClient",
    symbol: str,
    start: str,
    end: str,
    period: str = "D",
) -> list[KISDailyBar]:
    """Fetch daily OHLCV bars from KIS for a single KRX symbol.

    Parameters
    ----------
    client:  Configured KISClient instance.
    symbol:  KRX stock code (6 digits), e.g. "005930".
    start:   Start date "YYYYMMDD".
    end:     End date "YYYYMMDD".
    period:  "D" (daily) | "W" (weekly) | "M" (monthly). Default "D".

    Returns
    -------
    list[KISDailyBar], chronological order (oldest first). Empty list if no data.
    """
    all_bars: list[KISDailyBar] = []
    fk_token = ""
    nk_token = ""
    first_call = True

    while True:
        params: dict[str, str] = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end,
            "FID_PERIOD_DIV_CODE": period,
            "FID_ORG_ADJ_PRC": "0",
            "CTX_AREA_FK100": fk_token,
            "CTX_AREA_NK100": nk_token,
        }

        if not first_call:
            time.sleep(_RATE_LIMIT_SLEEP)

        data = _call_with_429_retry(client, params)
        first_call = False

        rows = data.get("output2") or []
        if not rows:
            break

        for row in rows:
            if not isinstance(row, dict):
                continue
            date_val = row.get("stck_bsop_date", "")
            if not date_val:
                continue
            bar = KISDailyBar(
                date=date_val,
                open=row.get("stck_oprc", "0"),
                high=row.get("stck_hgpr", "0"),
                low=row.get("stck_lwpr", "0"),
                close=row.get("stck_clpr", "0"),
                volume=row.get("acml_vol", "0"),
                trade_amt=row.get("acml_tr_pbmn", "0"),
            )
            all_bars.append(bar)

        # Check continuation header
        tr_cont = data.get("tr_cont", "")
        if tr_cont in ("F", "M"):
            fk_token = data.get("ctx_area_fk100", "")
            nk_token = data.get("ctx_area_nk100", "")
        else:
            break

    # KIS returns newest-first; reverse to chronological
    all_bars.reverse()
    return all_bars
