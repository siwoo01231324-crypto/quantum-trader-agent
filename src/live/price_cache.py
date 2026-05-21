"""Thread-safe latest-mark-price cache, populated by ``BinanceMarkPriceFeed``.

Used by the dashboard to surface live PnL% per open position. The cache is
write-heavy (one mark-price stream pushes ~hundreds of symbols/second) but
read-light (dashboard polls every 5s), so the implementation is just a
locked ``dict``. No external dependencies, no I/O.

Wired by:
  - ``src/live/loop.py:_run_mark_price_consumer`` calls ``set_price`` per
    batch entry when ``ShadowConfig.live_price_cache`` is supplied.
  - ``src/dashboard/app.py`` reads via ``DashboardState.price_cache`` to
    overlay ``mark_price`` + ``pnl_pct`` on ``/api/strategy_positions`` rows.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class PriceSnapshot:
    """One mark-price observation. ``ts`` is tz-aware UTC."""
    price: Decimal
    ts: datetime


class LivePriceCache:
    """Lock-protected ``dict[str, PriceSnapshot]`` keyed by upper-case symbol.

    Methods are deliberately minimal:
      - ``set_price(symbol, price, ts)`` — write one observation
      - ``get_price(symbol) -> PriceSnapshot | None`` — atomic read
      - ``snapshot() -> dict[str, PriceSnapshot]`` — atomic copy for bulk reads

    No staleness eviction: dashboard renders ``ts`` so the operator can spot
    a stale symbol themselves. Eviction would race with the producer for
    little gain — the consumer side already tolerates ``None``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prices: dict[str, PriceSnapshot] = {}

    def set_price(self, symbol: str, price: Decimal, ts: datetime) -> None:
        if not isinstance(price, Decimal):
            price = Decimal(str(price))
        sym = symbol.upper()
        with self._lock:
            self._prices[sym] = PriceSnapshot(price=price, ts=ts)

    def get_price(self, symbol: str) -> PriceSnapshot | None:
        with self._lock:
            return self._prices.get(symbol.upper())

    def snapshot(self) -> dict[str, PriceSnapshot]:
        with self._lock:
            return dict(self._prices)

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)
