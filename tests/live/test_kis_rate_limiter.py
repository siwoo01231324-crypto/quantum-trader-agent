"""Test Stage 2.1: KisRateLimiter token-bucket — raise-immediately semantics."""
from __future__ import annotations

import asyncio
import time

import pytest
from prometheus_client import CollectorRegistry

from src.brokers.errors import RateLimitError
from src.brokers.kis.rate_limiter import KisRateLimiter
from src.observability.metrics import Metrics


def _make_metrics() -> Metrics:
    return Metrics(registry=CollectorRegistry())


# ---------------------------------------------------------------------------
# paper mode: burst=2, refill_rate=2/sec
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_paper_burst_exhausted_raises():
    """3 simultaneous acquires with burst=2 → 3rd raises RateLimitError."""
    limiter = KisRateLimiter.for_paper()
    await limiter.acquire()
    await limiter.acquire()
    with pytest.raises(RateLimitError):
        await limiter.acquire()


@pytest.mark.asyncio
async def test_paper_refill_after_1sec():
    """After 1 second refill, acquire succeeds again."""
    limiter = KisRateLimiter.for_paper()
    await limiter.acquire()
    await limiter.acquire()
    with pytest.raises(RateLimitError):
        await limiter.acquire()
    # wait for refill
    await asyncio.sleep(1.1)
    # should succeed now (at least 2 tokens refilled)
    await limiter.acquire()


# ---------------------------------------------------------------------------
# live mode: burst=20
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_live_burst_20():
    """Live mode allows 20 consecutive acquires without error."""
    limiter = KisRateLimiter.for_live()
    for _ in range(20):
        await limiter.acquire()
    with pytest.raises(RateLimitError):
        await limiter.acquire()


# ---------------------------------------------------------------------------
# metric: qta_broker_rate_limit_hit_total
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_metric_incremented():
    """RateLimitError path must increment qta_broker_rate_limit_hit_total{broker,scope}."""
    m = _make_metrics()
    limiter = KisRateLimiter.for_paper(metrics=m)
    await limiter.acquire()
    await limiter.acquire()
    with pytest.raises(RateLimitError):
        await limiter.acquire()

    # Check counter incremented
    samples = list(m.broker_rate_limit_hit_total.collect())
    assert len(samples) > 0
    total = sum(
        s.value
        for metric in samples
        for s in metric.samples
        if s.labels.get("broker") == "kis" and s.labels.get("scope") == "paper"
    )
    assert total >= 1, "qta_broker_rate_limit_hit_total{broker=kis,scope=paper} not incremented"


# ---------------------------------------------------------------------------
# factory defaults
# ---------------------------------------------------------------------------

def test_for_paper_defaults():
    limiter = KisRateLimiter.for_paper()
    assert limiter.burst == 2
    assert limiter.refill_rate == 2.0


def test_for_live_defaults():
    limiter = KisRateLimiter.for_live()
    assert limiter.burst == 20
    assert limiter.refill_rate == 20.0
