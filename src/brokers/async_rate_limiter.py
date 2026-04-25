"""Async token-bucket rate limiter — "wait for token" semantics.

Semantics: when the bucket lacks tokens, callers await until tokens replenish.
This is fundamentally different from the sync RateLimiter in rate_limiter.py,
which raises RateLimitError immediately on exhaustion.

Rule: do NOT merge this file with rate_limiter.py.
The two files have incompatible semantics and must remain separate.
"""
from __future__ import annotations

import asyncio
import time


class AsyncTokenBucket:
    """Async token bucket that awaits replenishment instead of raising immediately.

    Concurrency is serialised with asyncio.Lock (FIFO fairness). asyncio.Semaphore
    is not used because token costs can vary (weight=1/5/10).

    Args:
        rate: tokens replenished per second.
        capacity: maximum token depth (burst ceiling).
    """

    def __init__(self, rate: float, capacity: float) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    async def acquire(self, cost: float = 1.0) -> None:
        """Wait until `cost` tokens are available, then consume them."""
        if cost <= 0:
            raise ValueError("cost must be positive")
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                # How long until enough tokens accumulate?
                deficit = cost - self._tokens
                wait_seconds = deficit / self._rate
                await asyncio.sleep(wait_seconds)

    def on_response_headers(self, headers: dict[str, str]) -> None:
        """Sync bucket state from exchange response headers (sync signature — called
        from httpx response hooks where no event loop context is available)."""
        pass  # Subclasses may override for header-based feedback


class AsyncBinanceRateLimiter:
    """Named-bucket async rate limiter for Binance USDS-M Futures.

    Buckets:
        weight     — request weight per minute (default 1200)
        orders_1m  — orders per minute (default 1200)
        orders_10s — orders per 10 seconds (default 300)
    """

    def __init__(self) -> None:
        self._buckets: dict[str, AsyncTokenBucket] = {}
        self.register_bucket("weight", rate=1200 / 60, capacity=1200)
        self.register_bucket("orders_1m", rate=1200 / 60, capacity=1200)
        self.register_bucket("orders_10s", rate=300 / 10, capacity=300)

    def register_bucket(self, name: str, rate: float, capacity: float) -> None:
        self._buckets[name] = AsyncTokenBucket(rate=rate, capacity=capacity)

    async def acquire(self, bucket: str, cost: float = 1.0) -> None:
        if bucket not in self._buckets:
            raise KeyError(f"Unknown rate-limit bucket: '{bucket}'")
        await self._buckets[bucket].acquire(cost)

    def on_response_headers(self, headers: dict[str, str]) -> None:
        """Sync call from httpx response hooks — adjust internal token counts."""
        if "X-MBX-USED-WEIGHT-1M" in headers and "weight" in self._buckets:
            used = float(headers["X-MBX-USED-WEIGHT-1M"])
            bucket = self._buckets["weight"]
            bucket._tokens = max(0.0, bucket._capacity - used)

        if "X-MBX-ORDER-COUNT-1M" in headers and "orders_1m" in self._buckets:
            used = float(headers["X-MBX-ORDER-COUNT-1M"])
            bucket = self._buckets["orders_1m"]
            bucket._tokens = max(0.0, bucket._capacity - used)

        if "X-MBX-ORDER-COUNT-10S" in headers and "orders_10s" in self._buckets:
            used = float(headers["X-MBX-ORDER-COUNT-10S"])
            bucket = self._buckets["orders_10s"]
            bucket._tokens = max(0.0, bucket._capacity - used)
