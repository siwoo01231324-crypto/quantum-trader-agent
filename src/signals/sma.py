"""Simple Moving Average + SMA crossover signal."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .registry import register


@register("sma", inputs=["close"], alpha_horizon_bars=10, bar_interval="1d", signal_type="trend", window=20)
def compute_sma(close: pd.Series, window: int = 20) -> pd.Series:
    """Rolling arithmetic mean. First `window-1` bars are NaN."""
    return close.rolling(window).mean()


@register("sma_cross", inputs=["close"], alpha_horizon_bars=10, bar_interval="1d", signal_type="trend", short_window=20, long_window=60)
def compute_sma_cross(
    close: pd.Series,
    short_window: int = 20,
    long_window: int = 60,
) -> pd.DataFrame:
    """SMA crossover signal.

    Emits ``"golden"`` on the bar where the short SMA crosses above the long SMA,
    ``"dead"`` on the bar where it crosses below, and ``None`` otherwise.
    Returns a DataFrame with columns ``sma_short``, ``sma_long``, ``signal``.
    """
    if short_window >= long_window:
        raise ValueError(f"short_window ({short_window}) must be < long_window ({long_window})")

    sma_short = close.rolling(short_window).mean()
    sma_long = close.rolling(long_window).mean()

    diff = sma_short - sma_long
    prev = diff.shift(1)
    signal = pd.Series([None] * len(close), index=close.index, dtype=object)
    signal[(prev <= 0) & (diff > 0)] = "golden"
    signal[(prev >= 0) & (diff < 0)] = "dead"

    return pd.DataFrame(
        {"sma_short": sma_short, "sma_long": sma_long, "signal": signal},
        index=close.index,
    )
