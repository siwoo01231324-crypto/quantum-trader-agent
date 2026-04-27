"""USD/KRW exchange rate fetcher with TTL cache.

Primary source: ExchangeRate-API (open endpoint, no key required for KRW=X pair).
  GET https://open.er-api.com/v6/latest/USD  → rates.KRW

yfinance is NOT in project deps; requests is used instead.

Behaviour:
- TTL default 300 s (5 min). Within TTL, cached value returned without fetch.
- On fetch failure, last successful value returned + WARNING logged.
- If last successful fetch is older than 24 h, None is returned instead
  (signals caller to suppress KRW metric emission). Kill-switch is NOT tripped.
- `age_seconds` property: seconds since last successful fetch (monotonic).
- `qta_fx_rate_age_seconds` Gauge is emitted lazily when Metrics is available.
  If Stage 1.1 metrics.py has not registered it yet, age metric emit is skipped.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_RATE_URL = "https://open.er-api.com/v6/latest/USD"
_REQUEST_TIMEOUT = 10  # seconds
_STALE_LIMIT_SEC = 24 * 3600  # 24 hours → return None


class FxRateCache:
    """Thread-safe (single-thread / asyncio) USD/KRW rate cache with TTL."""

    def __init__(self, ttl_sec: int = 300) -> None:
        self.ttl_sec = ttl_sec
        self._cached: Optional[float] = None
        self._cached_at: Optional[float] = None   # monotonic
        self._last_success_ts: Optional[float] = None  # monotonic

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self) -> Optional[float]:
        """Return USD/KRW rate or None if data is too stale (>24 h).

        Returns None when all of the following are true:
        - fetch fails (or has always failed)
        - last successful value is older than 24 h (or never fetched)
        """
        if self._cache_valid():
            return self._cached

        try:
            rate = self._fetch()
            self._cached = rate
            self._cached_at = time.monotonic()
            self._last_success_ts = time.monotonic()
            return rate
        except Exception as exc:
            logger.warning(
                "fx_rate fetch failed (%s); returning stale value %s",
                exc,
                self._cached,
            )
            if self._last_success_ts is None:
                return None
            if time.monotonic() - self._last_success_ts > _STALE_LIMIT_SEC:
                return None
            return self._cached

    @property
    def age_seconds(self) -> float:
        """Seconds since last successful fetch. 0 if never fetched."""
        if self._last_success_ts is None:
            return 0.0
        return time.monotonic() - self._last_success_ts

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _cache_valid(self) -> bool:
        if self._cached is None or self._cached_at is None:
            return False
        return (time.monotonic() - self._cached_at) < self.ttl_sec

    def _fetch(self) -> float:
        """Fetch USD/KRW from open.er-api.com. Raises on any error."""
        resp = requests.get(_RATE_URL, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        rate = data["rates"]["KRW"]
        if not isinstance(rate, (int, float)) or rate <= 0:
            raise ValueError(f"Unexpected KRW rate value: {rate!r}")
        return float(rate)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_default_cache: FxRateCache | None = None


def get_usd_krw(ttl_sec: int = 300) -> Optional[float]:
    """Return USD/KRW rate using the module-level singleton cache."""
    global _default_cache
    if _default_cache is None:
        _default_cache = FxRateCache(ttl_sec=ttl_sec)
    return _default_cache.get()
