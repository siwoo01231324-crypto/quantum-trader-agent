"""Turning point features — swing high/low reversal detection.

Rule #12 in Iranyi 12-rule stack.

A turning point is detected at bar t when:
  - The window [t-lookback, t-1] contains a local extreme (high or low), AND
  - Price at t reverses direction from that extreme.

Direction-agnostic: returns True at any reversal (both long and short).
For long-only strategies, combine with is_local_low_then_up().
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _local_high(arr: np.ndarray) -> bool:
    """True if the last value of arr is the maximum."""
    if len(arr) < 2:
        return False
    return bool(arr[-1] == arr.max())


def _local_low(arr: np.ndarray) -> bool:
    """True if the last value of arr is the minimum."""
    if len(arr) < 2:
        return False
    return bool(arr[-1] == arr.min())


def is_turning_point(close: pd.Series, lookback: int = 5) -> pd.Series:
    """True at bar t if [t-lookback..t-1] has a local high or low AND price reverses at t.

    Direction-agnostic: returns Boolean Series (True at any reversal).
    Returns False on NaN-containing windows or short window.

    Parameters
    ----------
    close:
        Per-bar close prices.
    lookback:
        Number of prior bars to scan for a local extreme.

    Returns
    -------
    pd.Series[bool]
    """
    result = pd.Series(False, index=close.index, dtype=bool)
    arr = close.to_numpy(dtype=float)
    n = len(arr)

    for t in range(lookback + 1, n):
        # window = [t-lookback .. t-1] (the prior lookback bars)
        window = arr[t - lookback : t]
        curr_close = arr[t]

        if not np.isfinite(window).all():
            continue
        if not np.isfinite(curr_close):
            continue

        win_max = window.max()
        win_min = window.min()
        prev_close = arr[t - 1]

        # Swing high: prior window contains the local high AND current bar reverses down
        had_high = win_max == prev_close or prev_close >= win_max
        # Swing low: prior window contains the local low AND current bar reverses up
        had_low = win_min == prev_close or prev_close <= win_min

        if had_high and curr_close < prev_close:
            result.iloc[t] = True
        elif had_low and curr_close > prev_close:
            result.iloc[t] = True

    return result.rename("is_turning_point")


def is_local_low_then_up(close: pd.Series, lookback: int = 5) -> pd.Series:
    """True at bar t if prior window has a local low AND price recovers upward at t.

    Suitable for long-only entry filtering: look for dip followed by recovery.

    Parameters
    ----------
    close:
        Per-bar close prices.
    lookback:
        Number of prior bars to scan for local low.

    Returns
    -------
    pd.Series[bool]
    """
    result = pd.Series(False, index=close.index, dtype=bool)
    arr = close.to_numpy(dtype=float)
    n = len(arr)

    for t in range(lookback + 1, n):
        # window = [t-lookback .. t-1]
        window = arr[t - lookback : t]
        curr_close = arr[t]

        if not np.isfinite(window).all():
            continue
        if not np.isfinite(curr_close):
            continue

        win_min = window.min()
        prev_close = arr[t - 1]

        # A dip occurred in the window: minimum is strictly less than the
        # first bar of the window (price went down at some point).
        had_low = win_min < window[0]
        reverses_up = curr_close > prev_close

        if had_low and reverses_up:
            result.iloc[t] = True

    return result.rename("is_local_low_then_up")
