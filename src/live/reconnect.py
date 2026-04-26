from __future__ import annotations
import asyncio
import random
from collections.abc import Awaitable, Callable

DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_BACKOFF_CAP = 60.0
DEFAULT_JITTER_FRAC = 0.25
DEFAULT_MAX_ATTEMPTS = 20


def backoff_delay(
    attempt: int,
    *,
    base: float = DEFAULT_BACKOFF_BASE,
    cap: float = DEFAULT_BACKOFF_CAP,
    jitter_frac: float = DEFAULT_JITTER_FRAC,
) -> float:
    """지수 backoff + uniform jitter [0, raw*jitter_frac]."""
    raw = min(base * (2 ** attempt), cap)
    if jitter_frac <= 0:
        return raw
    jitter = random.uniform(0.0, raw * jitter_frac)
    return raw + jitter


async def with_reconnect(
    coro_factory: Callable[[], Awaitable[None]],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    on_disconnect: Callable[[int, BaseException], None] | None = None,
    base: float = DEFAULT_BACKOFF_BASE,
    cap: float = DEFAULT_BACKOFF_CAP,
    jitter_frac: float = DEFAULT_JITTER_FRAC,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """coro_factory 를 max_attempts 까지 재시도. 매 실패 시 backoff_delay 만큼 sleep."""
    attempt = 0
    while attempt < max_attempts:
        try:
            await coro_factory()
            return  # 정상 종료
        except BaseException as err:
            if on_disconnect is not None:
                on_disconnect(attempt, err)
            attempt += 1
            if attempt >= max_attempts:
                raise RuntimeError(f"with_reconnect exhausted {max_attempts} attempts") from err
            delay = backoff_delay(attempt - 1, base=base, cap=cap, jitter_frac=jitter_frac)
            await sleep(delay)
