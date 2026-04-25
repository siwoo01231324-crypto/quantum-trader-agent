"""Unit tests for exponential_backoff and backoff_sequence."""
from __future__ import annotations

import asyncio
import math
from unittest.mock import AsyncMock, patch

import pytest

from src.brokers.async_backoff import backoff_sequence, exponential_backoff


@pytest.mark.asyncio
async def test_exponential_backoff_delays_increase():
    """Each attempt's base delay doubles, capped at cap."""
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    with patch("src.brokers.async_backoff.asyncio.sleep", side_effect=fake_sleep):
        for attempt in range(5):
            await exponential_backoff(attempt, base=1.0, cap=10.0, jitter=0.0)

    # With jitter=0 the noise term is 0 * ... = 0, so delays are exact powers of 2
    expected = [1.0, 2.0, 4.0, 8.0, 10.0]  # capped at 10
    for actual, exp in zip(delays, expected):
        assert math.isclose(actual, exp, rel_tol=1e-9), f"Expected {exp}, got {actual}"


@pytest.mark.asyncio
async def test_exponential_backoff_cap_enforced():
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    with patch("src.brokers.async_backoff.asyncio.sleep", side_effect=fake_sleep):
        # Attempt 10 would be 1024s without cap
        await exponential_backoff(10, base=1.0, cap=10.0, jitter=0.0)

    assert delays[0] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_exponential_backoff_jitter_within_range():
    """With jitter=0.2, final delay must stay within ±20% of base delay."""
    import random

    random.seed(42)
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    with patch("src.brokers.async_backoff.asyncio.sleep", side_effect=fake_sleep):
        for attempt in range(5):
            await exponential_backoff(attempt, base=1.0, cap=10.0, jitter=0.2)

    base_delays = [min(1.0 * (2 ** i), 10.0) for i in range(5)]
    for actual, base_d in zip(delays, base_delays):
        lo = base_d * (1 - 0.2)
        hi = base_d * (1 + 0.2)
        assert lo <= actual <= hi, f"Delay {actual:.3f} outside [{lo:.3f}, {hi:.3f}]"


@pytest.mark.asyncio
async def test_exponential_backoff_invalid_attempt():
    with pytest.raises(ValueError, match="attempt must be >= 0"):
        await exponential_backoff(-1)


@pytest.mark.asyncio
async def test_exponential_backoff_invalid_base():
    with pytest.raises(ValueError, match="base must be positive"):
        await exponential_backoff(0, base=0.0)


@pytest.mark.asyncio
async def test_exponential_backoff_invalid_cap():
    with pytest.raises(ValueError, match="cap must be >= base"):
        await exponential_backoff(0, base=5.0, cap=1.0)


@pytest.mark.asyncio
async def test_exponential_backoff_invalid_jitter():
    with pytest.raises(ValueError, match="jitter must be in"):
        await exponential_backoff(0, jitter=1.5)


@pytest.mark.asyncio
async def test_backoff_sequence_yields_all_attempts():
    """backoff_sequence yields each attempt index 0..max_attempts-1."""
    attempts: list[int] = []

    async def fake_sleep(seconds: float) -> None:
        pass

    with patch("src.brokers.async_backoff.asyncio.sleep", side_effect=fake_sleep):
        async for attempt in backoff_sequence(5, base=1.0, cap=10.0, jitter=0.0):
            attempts.append(attempt)

    assert attempts == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_backoff_sequence_sleeps_between_attempts_not_after_last():
    """backoff_sequence must sleep N-1 times for N attempts."""
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    with patch("src.brokers.async_backoff.asyncio.sleep", side_effect=fake_sleep):
        async for _ in backoff_sequence(5, base=1.0, cap=10.0, jitter=0.0):
            pass

    assert len(sleep_calls) == 4  # N-1 sleeps for N=5


@pytest.mark.asyncio
async def test_backoff_sequence_max_5_cap_10_jitter_20pct():
    """Plan-specified gate: max 5 attempts, cap 10s, jitter ±20%."""
    import random
    random.seed(0)
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    with patch("src.brokers.async_backoff.asyncio.sleep", side_effect=fake_sleep):
        async for _ in backoff_sequence(5, base=1.0, cap=10.0, jitter=0.2):
            pass

    assert len(sleep_calls) == 4
    base_delays = [min(1.0 * (2 ** i), 10.0) for i in range(4)]
    for actual, base_d in zip(sleep_calls, base_delays):
        lo = base_d * (1 - 0.2)
        hi = base_d * (1 + 0.2)
        assert lo <= actual <= hi, f"Sleep {actual:.3f} outside [{lo:.3f}, {hi:.3f}]"
        assert actual <= 10.0, f"Sleep {actual:.3f} exceeds cap=10s"
