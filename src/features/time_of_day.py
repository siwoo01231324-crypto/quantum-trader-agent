"""Time-of-day / day-of-week trading gate.

Reference: ``docs/background/42-cross-sectional-momentum-crypto.md`` §6.

Variant D parameter freeze (issue #99 comment 4322461471):
``time_gate(blocked_hours=[((10, 30), (11, 0))], block_weekends=True,
timezone="Asia/Seoul")`` — faithful-to-source choice.
"""
from __future__ import annotations

from datetime import time

import pandas as pd

DEFAULT_BLOCKED_HOURS: list[tuple[tuple[int, int], tuple[int, int]]] = [
    ((10, 30), (11, 0)),
]


def time_gate(
    index: pd.DatetimeIndex,
    blocked_hours: list[tuple[tuple[int, int], tuple[int, int]]] | None = None,
    block_weekends: bool = True,
    timezone: str = "Asia/Seoul",
) -> pd.Series:
    """Boolean trading gate: ``True`` => trading allowed, ``False`` => blocked.

    Parameters
    ----------
    index:
        DatetimeIndex of bar timestamps. May be naive or tz-aware.
    blocked_hours:
        List of ``((start_h, start_m), (end_h, end_m))`` ranges.
        Each range is treated as half-open ``[start, end)``. Default
        is ``[((10, 30), (11, 0))]``: 10:30–11:00 in ``timezone``.
    block_weekends:
        If True, Saturday and Sunday (in ``timezone``) are blocked.
    timezone:
        Timezone (``zoneinfo``-compatible) for evaluating blocked hours
        and weekday. Default ``"Asia/Seoul"`` (KST) per Variant D.

    Returns
    -------
    pd.Series[bool]
        Indexed by ``index``. ``True`` if trading is allowed at that
        timestamp.
    """
    if blocked_hours is None:
        blocked_hours = DEFAULT_BLOCKED_HOURS

    # Move into the chosen timezone for hour/weekday evaluation.
    if index.tz is None:
        local_idx = index.tz_localize("UTC").tz_convert(timezone)
    else:
        local_idx = index.tz_convert(timezone)

    allowed = pd.Series(True, index=index, name="time_gate")

    if block_weekends:
        wk = local_idx.dayofweek  # Mon=0 .. Sun=6
        allowed &= ~((wk == 5) | (wk == 6))

    if blocked_hours:
        local_time = local_idx.time
        for (sh, sm), (eh, em) in blocked_hours:
            start_t = time(hour=sh, minute=sm)
            end_t = time(hour=eh, minute=em)
            in_range = pd.Series(
                [
                    _is_in_range(t, start_t, end_t)
                    for t in local_time
                ],
                index=index,
            )
            allowed &= ~in_range

    return allowed


def _is_in_range(t: time, start: time, end: time) -> bool:
    """Half-open membership test ``[start, end)`` for time-of-day."""
    if start <= end:
        return start <= t < end
    # Wrap-around (e.g., 23:00–01:00). Two intervals.
    return t >= start or t < end
