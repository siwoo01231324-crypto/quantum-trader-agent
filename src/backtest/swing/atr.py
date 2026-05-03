"""Wilder ATR (Average True Range) for swing strategy stop-loss variants.

Wilder's smoothing uses alpha = 1/period (equivalent to EWM with alpha=1/n).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Wilder ATR(period) from OHLCV DataFrame.

    Requires columns: high, low, close.

    Parameters
    ----------
    df     : OHLCV DataFrame with 'high', 'low', 'close' columns.
    period : ATR lookback period (default 14).

    Returns
    -------
    pd.Series of ATR values, indexed same as df. First (period-1) values are NaN.
    """
    high = df["high"]
    low = df["low"]
    close_prev = df["close"].shift(1)

    tr1 = high - low
    tr2 = (high - close_prev).abs()
    tr3 = (low - close_prev).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Wilder smoothing: EWM with alpha = 1/period
    atr = true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return atr.rename("atr")
