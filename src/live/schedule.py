"""Live loop schedule gate (#216).

Provides ``wait_until_session_open`` — an async helper that blocks until the
next KRX trading session opens when ``schedule='krx'`` and the current moment
is outside session hours. ``schedule='always'`` returns immediately so the
existing 24/7 behaviour is preserved.

Why this module exists
----------------------
Prior to #216 ``scripts/live_run.py`` accepted ``--schedule={krx,always}`` but
the value was never inspected. As a result startup outside KRX hours kicked
off ``snapshot_builder.warmup`` immediately, which caused a stream of
``EGW00201`` (rate-limit) warnings against the KIS paper server while no live
ticks could ever flow (WS connect happens *after* warmup). The whole live
pipeline silently stalled and the WAL stayed empty.

This helper is the single point that the live loop and ``live_run`` consult
to decide whether to wait. It is intentionally side-effect free apart from
calling the injected ``sleep_fn``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable

from src.universe.krx_calendar import KST, is_krx_trading_hours, next_session_open

log = logging.getLogger(__name__)

ScheduleMode = str  # "krx" | "always" — string for argparse compat


def _now_kst() -> datetime:
    return datetime.now(tz=KST)


async def _async_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def wait_until_session_open(
    schedule: ScheduleMode,
    *,
    now_fn: Callable[[], datetime] = _now_kst,
    sleep_fn: Callable[[float], Awaitable[None]] = _async_sleep,
) -> datetime:
    """Block until the next KRX session open if ``schedule='krx'`` and we are
    currently outside session hours. Otherwise return ``now_fn()`` immediately.

    Args:
        schedule: ``"krx"`` (gate) or ``"always"`` (no-op).
        now_fn: callable returning the current timezone-aware datetime
            (defaults to KST wall-clock). Tests inject a fixed clock.
        sleep_fn: async sleep function (defaults to ``asyncio.sleep``).
            Tests inject ``AsyncMock`` to assert deterministic delays.

    Returns:
        Timezone-aware datetime at which control resumes. For ``"always"``
        and within-session ``"krx"`` this is ``now_fn()``; for outside-session
        ``"krx"`` this is the next session open computed by
        :func:`src.universe.krx_calendar.next_session_open`.

    Raises:
        ValueError: ``schedule`` is neither ``"krx"`` nor ``"always"``.
    """
    if schedule == "always":
        return now_fn()
    if schedule != "krx":
        raise ValueError(
            f"unknown schedule mode: {schedule!r} (expected 'krx' or 'always')"
        )

    now = now_fn()
    if is_krx_trading_hours(now):
        return now

    target = next_session_open(now)
    delay = (target - now).total_seconds()
    if delay <= 0:
        # Edge: clock raced past the target between checks. Skip sleep.
        return now

    log.info(
        "live.schedule outside session, sleeping %.0fs (~%.1fh) until %s KST",
        delay, delay / 3600.0, target.isoformat(),
    )
    await sleep_fn(delay)
    return target
