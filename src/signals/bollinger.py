"""Bollinger Bands + %B + BandWidth."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .registry import register


@register("bollinger", inputs=["close"], window=20, n_std=2.0)
def compute_bollinger(
    close: pd.Series,
    window: int = 20,
    n_std: float = 2.0,
) -> pd.DataFrame:
    """Classic Bollinger Bands.

    Uses population std (``ddof=0``) to match TA-Lib / pandas-ta convention.
    Returns a DataFrame with columns ``upper``, ``middle``, ``lower``, ``pct_b``, ``bandwidth``.
    """
    middle = close.rolling(window).mean()
    std = close.rolling(window).std(ddof=0)
    upper = middle + n_std * std
    lower = middle - n_std * std

    band = upper - lower
    pct_b = (close - lower) / band.where(band != 0, np.nan)
    bandwidth = band / middle.where(middle != 0, np.nan)

    return pd.DataFrame(
        {
            "upper": upper,
            "middle": middle,
            "lower": lower,
            "pct_b": pct_b,
            "bandwidth": bandwidth,
        },
        index=close.index,
    )
