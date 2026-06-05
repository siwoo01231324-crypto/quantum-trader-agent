"""Bitget contract filter (LOT_SIZE-equivalent) with TTL cache.

Mirrors ``src/brokers/binance/symbol_filters.py`` API surface:
  - lot_step(symbol) → Decimal qty stepSize
  - min_qty(symbol)  → Decimal minimum order size
  - tick_size(symbol) → Decimal price tick
  - quantize_price(symbol, px) → Decimal aligned to tick

Bitget contract metadata comes from ``GET /api/v2/mix/market/contracts``.
TTL: 6 hours (contracts rarely change; refresh on cache miss).

Sync HTTP (httpx.Client) is used because :class:`AsyncBinanceFuturesAdapter`'s
quantize helpers are sync (and the call is rare: once per symbol per session).
"""
from __future__ import annotations

import logging
import threading
import time
from decimal import Decimal

import httpx

from src.brokers.bitget.async_http import (
    DEMO_PRODUCT_TYPE,
    LIVE_PRODUCT_TYPE,
    REST_BASE_LIVE,
)
from src.brokers.errors import ValidationError

log = logging.getLogger(__name__)

_CACHE_TTL_SEC = 6 * 3600


class SymbolFilters:
    """Sync cache of Bitget contract filters by symbol."""

    def __init__(
        self,
        *,
        base_url: str = REST_BASE_LIVE,
        paper: bool = True,
        product_type: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._product_type = product_type or (DEMO_PRODUCT_TYPE if paper else LIVE_PRODUCT_TYPE)
        self._cache: dict[str, dict] = {}
        self._fetched_at: float = 0.0
        self._lock = threading.Lock()

    def _refresh(self) -> None:
        with httpx.Client(timeout=15.0) as c:
            r = c.get(
                f"{self._base_url}/api/v2/mix/market/contracts",
                params={"productType": self._product_type},
            )
        if r.status_code != 200:
            raise BrokerFilterFetchError(  # type: ignore[name-defined]
                f"contracts fetch failed: status={r.status_code} body={r.text[:200]}"
            )
        data = r.json()
        if str(data.get("code")) != "00000":
            raise BrokerFilterFetchError(  # type: ignore[name-defined]
                f"contracts code={data.get('code')} msg={data.get('msg')}"
            )
        new_cache: dict[str, dict] = {}
        for c in data.get("data") or []:
            new_cache[c["symbol"]] = c
        self._cache = new_cache
        self._fetched_at = time.time()
        log.info("bitget contracts refreshed: %d symbols", len(new_cache))

    def _get(self, symbol: str) -> dict:
        with self._lock:
            stale = (time.time() - self._fetched_at) > _CACHE_TTL_SEC
            if not self._cache or stale or symbol not in self._cache:
                self._refresh()
            entry = self._cache.get(symbol)
            if entry is None:
                raise ValidationError(f"unknown symbol: {symbol}")
            return entry

    # ── public API ────────────────────────────────────────────────────────────

    def lot_step(self, symbol: str) -> Decimal:
        return Decimal(str(self._get(symbol).get("sizeMultiplier", "1")))

    def min_qty(self, symbol: str) -> Decimal:
        return Decimal(str(self._get(symbol).get("minTradeNum", "0")))

    def tick_size(self, symbol: str) -> Decimal:
        c = self._get(symbol)
        # tick = priceEndStep / 10**pricePlace.
        end = int(c.get("priceEndStep", 1) or 1)
        place = int(c.get("pricePlace", 0) or 0)
        if place == 0:
            return Decimal(end)
        return Decimal(end) / (Decimal(10) ** place)

    def quantize_price(self, symbol: str, price: Decimal) -> Decimal:
        tick = self.tick_size(symbol)
        if tick <= 0:
            return price
        # Round DOWN to tick (mirrors Binance behaviour for LIMIT submission).
        return (price // tick) * tick


class BrokerFilterFetchError(ValidationError):
    """Raised when /contracts cannot be fetched (HTTP error or non-zero code)."""
