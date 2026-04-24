"""Average True Range with Wilder (SMMA) smoothing."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .registry import register


@register("atr", inputs=["high", "low", "close"], alpha_horizon_bars=1, bar_interval="1d", signal_type="volatility", window=14)
def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 14,
) -> pd.Series:
    """True Range then Wilder EMA. First `window` bars are NaN.

    TR_i = max(H_i - L_i, |H_i - C_{i-1}|, |L_i - C_{i-1}|) for i >= 1.
    Seed ATR at bar `window` = mean(TR_1..TR_window).
    ATR_i = (ATR_{i-1} * (window-1) + TR_i) / window for i > window.
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = pd.Series(np.nan, index=close.index, dtype=float)
    if len(close) <= window:
        return atr

    # Seed at index `window` = mean of TR_1..TR_window (indices 1..window)
    seed = tr.iloc[1 : window + 1].mean()
    atr.iloc[window] = seed
    for i in range(window + 1, len(close)):
        atr.iloc[i] = (atr.iloc[i - 1] * (window - 1) + tr.iloc[i]) / window
    return atr
