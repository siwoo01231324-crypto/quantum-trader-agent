"""Donchian channel factor."""
from __future__ import annotations

import pandas as pd

from .registry import register


@register("donchian", inputs=["high", "low"], signal_type="breakout", bar_interval="1d", alpha_horizon_bars=20, window=20)
def compute_donchian(
    high: pd.Series,
    low: pd.Series,
    window: int = 20,
) -> pd.DataFrame:
    """Donchian channel upper/lower/middle bands.

    upper = rolling max of high over `window` bars.
    lower = rolling min of low over `window` bars.
    middle = (upper + lower) / 2.
    First `window - 1` bars are NaN (warmup).
    """
    upper = high.rolling(window).max()
    lower = low.rolling(window).min()
    middle = (upper + lower) / 2.0

    return pd.DataFrame(
        {
            "upper": upper,
            "lower": lower,
            "middle": middle,
        },
        index=high.index,
    )
