"""Async exponential backoff generator for WS reconnect and retry loops."""
from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator


async def exponential_backoff(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 10.0,
    jitter: float = 0.2,
) -> None:
    """Await an exponentially increasing delay with bounded jitter.

    Args:
        attempt: zero-based retry count (0 = first retry after first failure).
        base: base delay in seconds.
        cap: maximum delay in seconds.
        jitter: fractional jitter range applied symmetrically (0.2 = ±20%).
    """
    if attempt < 0:
        raise ValueError("attempt must be >= 0")
    if base <= 0:
        raise ValueError("base must be positive")
    if cap < base:
        raise ValueError("cap must be >= base")
    if not (0.0 <= jitter <= 1.0):
        raise ValueError("jitter must be in [0, 1]")

    delay = min(base * (2 ** attempt), cap)
    noise = delay * jitter * (2 * random.random() - 1)  # uniform in [-jitter*delay, +jitter*delay]
    final = max(0.0, delay + noise)
    await asyncio.sleep(final)


async def backoff_sequence(
    max_attempts: int,
    *,
    base: float = 1.0,
    cap: float = 10.0,
    jitter: float = 0.2,
) -> AsyncIterator[int]:
    """Async generator: yield attempt index (0..max_attempts-1), sleeping between yields.

    Usage::

        async for attempt in backoff_sequence(5, base=1.0, cap=10.0):
            try:
                await do_something()
                break
            except SomeError:
                pass  # backoff sleep happens automatically before next iteration
    """
    for attempt in range(max_attempts):
        yield attempt
        if attempt < max_attempts - 1:
            await exponential_backoff(attempt, base=base, cap=cap, jitter=jitter)
