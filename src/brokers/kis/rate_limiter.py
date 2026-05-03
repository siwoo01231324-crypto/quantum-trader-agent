"""KIS-specific async token-bucket rate limiter — raise-immediately semantics.

KIS rate limits (docs/background/10-broker-api-comparison.md §1):
  paper: 2 RPS burst, 2 tokens/sec refill
  live:  20 RPS burst, 20 tokens/sec refill

Unlike AsyncTokenBucket (async_rate_limiter.py) which *waits* for tokens,
this limiter raises RateLimitError immediately when burst is exhausted.
Do NOT merge with async_rate_limiter.py — semantics are intentionally different.
"""
from __future__ import annotations

import asyncio
import time

from src.brokers.errors import RateLimitError
from src.observability.metrics import Metrics


class KisRateLimiter:
    """Async token-bucket for KIS REST API — raises RateLimitError on exhaustion.

    Args:
        burst: Maximum token depth (initial tokens = burst).
        refill_rate: Tokens replenished per second.
        scope: Label for qta_broker_rate_limit_hit_total{scope=...}.
        metrics: Metrics instance for Prometheus counter. If None, no metric emitted.
    """

    def __init__(
        self,
        burst: int,
        refill_rate: float,
        scope: str = "unknown",
        metrics: Metrics | None = None,
    ) -> None:
        if burst <= 0:
            raise ValueError("burst must be positive")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be positive")
        self.burst = burst
        self.refill_rate = refill_rate
        self._scope = scope
        self._metrics = metrics
        self._tokens: float = float(burst)
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(float(self.burst), self._tokens + elapsed * self.refill_rate)
        self._last_refill = now

    async def acquire(self, cost: float = 1.0) -> None:
        """Consume `cost` tokens, or raise RateLimitError if insufficient."""
        async with self._lock:
            self._refill()
            if self._tokens < cost:
                if self._metrics is not None:
                    self._metrics.broker_rate_limit_hit_total.labels(
                        broker="kis", scope=self._scope
                    ).inc()
                raise RateLimitError(
                    f"KIS rate limit exceeded (scope={self._scope}): "
                    f"need {cost} tokens, have {self._tokens:.2f}"
                )
            self._tokens -= cost

    @classmethod
    def for_paper(cls, metrics: Metrics | None = None) -> "KisRateLimiter":
        """Factory for KIS paper (모의투자) mode: 2 RPS burst, 2/sec refill."""
        return cls(burst=2, refill_rate=2.0, scope="paper", metrics=metrics)

    @classmethod
    def for_live(cls, metrics: Metrics | None = None) -> "KisRateLimiter":
        """Factory for KIS live mode: 20 RPS burst, 20/sec refill."""
        return cls(burst=20, refill_rate=20.0, scope="live", metrics=metrics)
