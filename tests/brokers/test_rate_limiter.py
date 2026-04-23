from __future__ import annotations

import time

import pytest

from src.brokers.rate_limiter import RateLimiter
from src.brokers.errors import RateLimitError


def test_single_bucket_allows_within_capacity():
    rl = RateLimiter()
    rl.register_bucket("weight", rate=10, capacity=10)
    # should not raise for cost <= capacity
    for _ in range(10):
        rl.acquire("weight", cost=1)


def test_single_bucket_blocks_when_exhausted():
    rl = RateLimiter()
    rl.register_bucket("orders", rate=2, capacity=2)
    rl.acquire("orders", cost=1)
    rl.acquire("orders", cost=1)
    with pytest.raises(RateLimitError):
        rl.acquire("orders", cost=1)


def test_multiple_buckets_independent():
    rl = RateLimiter()
    rl.register_bucket("weight", rate=100, capacity=100)
    rl.register_bucket("orders_1m", rate=10, capacity=10)
    # weight bucket: high capacity
    for _ in range(5):
        rl.acquire("weight", cost=5)
    # orders bucket still has capacity
    rl.acquire("orders_1m", cost=1)


def test_acquire_cost_greater_than_one():
    rl = RateLimiter()
    rl.register_bucket("weight", rate=100, capacity=20)
    rl.acquire("weight", cost=15)
    with pytest.raises(RateLimitError):
        rl.acquire("weight", cost=10)


def test_unknown_bucket_raises():
    rl = RateLimiter()
    with pytest.raises(KeyError):
        rl.acquire("nonexistent", cost=1)


def test_on_response_headers_reduces_weight_bucket():
    rl = RateLimiter()
    rl.register_bucket("weight", rate=6000, capacity=6000)
    # Pre-fill to known state: acquire 100
    rl.acquire("weight", cost=100)
    # Binance header says used=5500 → remaining=500
    headers = {"X-MBX-USED-WEIGHT-1M": "5500"}
    rl.on_response_headers(headers)
    # Now only 500 left; 501 should fail
    with pytest.raises(RateLimitError):
        rl.acquire("weight", cost=501)
    # 500 should succeed
    rl2 = RateLimiter()
    rl2.register_bucket("weight", rate=6000, capacity=6000)
    rl2.on_response_headers(headers)
    rl2.acquire("weight", cost=500)


def test_on_response_headers_reduces_order_bucket():
    rl = RateLimiter()
    rl.register_bucket("orders_1m", rate=1200, capacity=1200)
    headers = {"X-MBX-ORDER-COUNT-1M": "1190"}
    rl.on_response_headers(headers)
    with pytest.raises(RateLimitError):
        rl.acquire("orders_1m", cost=11)
    rl.acquire("orders_1m", cost=10)


def test_register_same_bucket_twice_overwrites():
    rl = RateLimiter()
    rl.register_bucket("weight", rate=10, capacity=10)
    rl.register_bucket("weight", rate=100, capacity=100)
    # should have capacity 100 now
    rl.acquire("weight", cost=50)


def test_default_cost_is_one():
    rl = RateLimiter()
    rl.register_bucket("b", rate=1, capacity=1)
    rl.acquire("b")  # default cost=1
    with pytest.raises(RateLimitError):
        rl.acquire("b")
