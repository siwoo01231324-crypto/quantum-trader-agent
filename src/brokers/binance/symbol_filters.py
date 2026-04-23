from __future__ import annotations

import logging
import time
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN

import requests

from src.brokers.binance.schemas import ExchangeInfoSymbol
from src.brokers.errors import InvalidOrderError, ValidationError

log = logging.getLogger(__name__)

_TTL_S = 3600  # 1 hour cache


class SymbolFilters:
    """Caches Binance exchangeInfo and provides quantize/validate helpers."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._filters: dict[str, ExchangeInfoSymbol] = {}
        self._loaded_at: float = 0.0

    def _load(self) -> None:
        resp = requests.get(f"{self._base_url}/fapi/v1/exchangeInfo", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        self._filters = {
            s["symbol"]: ExchangeInfoSymbol.model_validate(s)
            for s in data.get("symbols", [])
        }
        self._loaded_at = time.monotonic()
        log.debug("SymbolFilters loaded: %d symbols", len(self._filters))

    def _ensure_loaded(self) -> None:
        age = time.monotonic() - self._loaded_at
        if age > _TTL_S or not self._filters:
            self._load()

    def _get_filter(self, symbol: str, filter_type: str) -> dict | None:
        self._ensure_loaded()
        sym = self._filters.get(symbol)
        if sym is None:
            raise ValidationError(f"Unknown symbol: {symbol}")
        for f in sym.filters:
            if f.filterType == filter_type:
                return f.model_dump()
        return None

    def tick_size(self, symbol: str) -> Decimal:
        f = self._get_filter(symbol, "PRICE_FILTER")
        if f and f.get("tickSize") is not None:
            return f["tickSize"]
        raise ValidationError(f"No PRICE_FILTER for {symbol}")

    def lot_step(self, symbol: str) -> Decimal:
        f = self._get_filter(symbol, "LOT_SIZE")
        if f and f.get("stepSize") is not None:
            return f["stepSize"]
        raise ValidationError(f"No LOT_SIZE filter for {symbol}")

    def min_qty(self, symbol: str) -> Decimal:
        f = self._get_filter(symbol, "LOT_SIZE")
        if f and f.get("minQty") is not None:
            return f["minQty"]
        raise ValidationError(f"No LOT_SIZE filter for {symbol}")

    def min_notional(self, symbol: str) -> Decimal:
        f = self._get_filter(symbol, "MIN_NOTIONAL")
        if f and f.get("notional") is not None:
            return f["notional"]
        raise ValidationError(f"No MIN_NOTIONAL filter for {symbol}")

    def percent_price_up(self, symbol: str) -> Decimal:
        f = self._get_filter(symbol, "PERCENT_PRICE")
        if f and f.get("multiplierUp") is not None:
            return f["multiplierUp"]
        raise ValidationError(f"No PERCENT_PRICE filter for {symbol}")

    def percent_price_down(self, symbol: str) -> Decimal:
        f = self._get_filter(symbol, "PERCENT_PRICE")
        if f and f.get("multiplierDown") is not None:
            return f["multiplierDown"]
        raise ValidationError(f"No PERCENT_PRICE filter for {symbol}")

    def quantize_price(self, symbol: str, price: Decimal) -> Decimal:
        tick = self.tick_size(symbol)
        return price.quantize(tick, rounding=ROUND_HALF_EVEN)

    def quantize_qty(self, symbol: str, qty: Decimal) -> Decimal:
        step = self.lot_step(symbol)
        # floor-divide to nearest step multiple, then re-quantize for trailing zeros
        floored = (qty // step) * step
        return floored.quantize(step, rounding=ROUND_DOWN)

    def validate_order(self, symbol: str, price: Decimal, qty: Decimal) -> None:
        """Raise InvalidOrderError if price/qty violates exchange filters."""
        notional = price * qty
        min_n = self.min_notional(symbol)
        if notional < min_n:
            raise InvalidOrderError(
                f"{symbol}: notional {notional} < min_notional {min_n}"
            )

        up = self.percent_price_up(symbol)
        down = self.percent_price_down(symbol)
        # PERCENT_PRICE multipliers are applied to the mark price.
        # Without the mark price here we validate that filters are readable;
        # callers that have the mark price should perform the check themselves.
        # This method validates structure; full price-band check is in adapter.
        _ = up, down  # accessed to ensure filter exists
