from __future__ import annotations

from dataclasses import dataclass, field

from src.brokers.errors import RateLimitError


@dataclass
class _Bucket:
    rate: float       # tokens replenished per second (informational)
    capacity: float   # max tokens
    tokens: float     # current available tokens

    def acquire(self, cost: float) -> None:
        if self.tokens < cost:
            raise RateLimitError(
                f"Rate limit exceeded: need {cost} tokens, have {self.tokens:.1f}"
            )
        self.tokens -= cost

    def set_used(self, used: float) -> None:
        remaining = max(0.0, self.capacity - used)
        self.tokens = remaining


class RateLimiter:
    """Token-bucket rate limiter with named buckets.

    Supports Binance X-MBX-* response header feedback to sync
    actual server-side usage.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}

    def register_bucket(self, name: str, rate: float, capacity: float) -> None:
        self._buckets[name] = _Bucket(rate=rate, capacity=capacity, tokens=capacity)

    def acquire(self, bucket: str, cost: float = 1) -> None:
        if bucket not in self._buckets:
            raise KeyError(f"Unknown rate-limit bucket: '{bucket}'")
        self._buckets[bucket].acquire(cost)

    def on_response_headers(self, headers: dict[str, str]) -> None:
        """Sync bucket state from Binance response headers."""
        if "X-MBX-USED-WEIGHT-1M" in headers:
            used = float(headers["X-MBX-USED-WEIGHT-1M"])
            if "weight" in self._buckets:
                self._buckets["weight"].set_used(used)

        if "X-MBX-ORDER-COUNT-1M" in headers:
            used = float(headers["X-MBX-ORDER-COUNT-1M"])
            if "orders_1m" in self._buckets:
                self._buckets["orders_1m"].set_used(used)

        if "X-MBX-ORDER-COUNT-10S" in headers:
            used = float(headers["X-MBX-ORDER-COUNT-10S"])
            if "orders_10s" in self._buckets:
                self._buckets["orders_10s"].set_used(used)
