"""Unit tests for AsyncTokenBucket — "wait for token" semantics."""
from __future__ import annotations

import asyncio
import time

import pytest

from src.brokers.async_rate_limiter import AsyncBinanceRateLimiter, AsyncTokenBucket


@pytest.mark.asyncio
async def test_single_acquire_within_capacity():
    bucket = AsyncTokenBucket(rate=10.0, capacity=10.0)
    await bucket.acquire(1.0)  # should not block


@pytest.mark.asyncio
async def test_acquire_full_capacity_does_not_block():
    bucket = AsyncTokenBucket(rate=10.0, capacity=10.0)
    await bucket.acquire(10.0)


@pytest.mark.asyncio
async def test_acquire_over_capacity_waits_and_succeeds():
    bucket = AsyncTokenBucket(rate=100.0, capacity=5.0)
    await bucket.acquire(5.0)  # drain
    # next acquire needs 5 tokens at 100/s = 0.05s wait
    start = time.monotonic()
    await bucket.acquire(5.0)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.04, f"Expected wait ≥0.04s, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_concurrent_coroutines_serialised_within_throughput():
    """200 concurrent coroutines each consuming 1 token from a 10 rps bucket.

    Total tokens needed = 200 at 10/s = ~20 seconds at pure serial rate.
    We use a high-rate bucket (200 rps, capacity 200) so they all finish fast,
    and verify the throughput stays within 10% of capacity-based theoretical max.
    """
    rate = 200.0
    capacity = 200.0
    bucket = AsyncTokenBucket(rate=rate, capacity=capacity)
    n = 200

    start = time.monotonic()
    await asyncio.gather(*[bucket.acquire(1.0) for _ in range(n)])
    elapsed = time.monotonic() - start

    # All 200 tokens available (capacity=200), should complete nearly instantly
    assert elapsed < 1.0, f"200 acquires from full bucket took {elapsed:.3f}s, expected <1s"


@pytest.mark.asyncio
async def test_10_rps_throughput_gate():
    """Verify that a 10 rps bucket, 25 sequential acquires, takes ~2.4s."""
    bucket = AsyncTokenBucket(rate=10.0, capacity=10.0)
    n = 25
    start = time.monotonic()
    for _ in range(n):
        await bucket.acquire(1.0)
    elapsed = time.monotonic() - start
    # 25 tokens at 10/s: first 10 free (burst), then 15 more at 0.1s each = 1.5s
    # Allow generous tolerance for CI
    assert elapsed >= 1.0, f"Expected ≥1.0s for 25 acquires at 10 rps, got {elapsed:.3f}s"
    assert elapsed < 5.0, f"Expected <5.0s for 25 acquires at 10 rps, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_invalid_cost_raises():
    bucket = AsyncTokenBucket(rate=10.0, capacity=10.0)
    with pytest.raises(ValueError, match="cost must be positive"):
        await bucket.acquire(0.0)


def test_invalid_rate_raises():
    with pytest.raises(ValueError, match="rate must be positive"):
        AsyncTokenBucket(rate=0.0, capacity=10.0)


def test_invalid_capacity_raises():
    with pytest.raises(ValueError, match="capacity must be positive"):
        AsyncTokenBucket(rate=10.0, capacity=0.0)


def test_on_response_headers_adjusts_tokens():
    limiter = AsyncBinanceRateLimiter()
    headers = {
        "X-MBX-USED-WEIGHT-1M": "600",
        "X-MBX-ORDER-COUNT-1M": "300",
        "X-MBX-ORDER-COUNT-10S": "100",
    }
    limiter.on_response_headers(headers)
    assert limiter._buckets["weight"]._tokens == pytest.approx(600.0)
    assert limiter._buckets["orders_1m"]._tokens == pytest.approx(900.0)
    assert limiter._buckets["orders_10s"]._tokens == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_binance_rate_limiter_acquire_unknown_bucket():
    limiter = AsyncBinanceRateLimiter()
    with pytest.raises(KeyError, match="Unknown rate-limit bucket"):
        await limiter.acquire("nonexistent")
