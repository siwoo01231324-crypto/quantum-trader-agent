"""Price-to-MA z-score feature (Iranyi rule #8, issue #185).

Rolling z-score of (close - MA) over `lookback` bars.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def price_ma_zscore(
    close: pd.Series,
    ma: pd.Series,
    lookback: int = 100,
) -> pd.Series:
    """Rolling z-score of (close - ma) over `lookback` bars.

    Parameters
    ----------
    close:
        Closing prices.
    ma:
        Moving average series (any window), same index as ``close``.
    lookback:
        Rolling window for computing mean and std of the distance.

    Returns
    -------
    pd.Series
        Z-score values. NaN during warmup or when rolling std is zero.
    """
    dist = close - ma
    roll_mean = dist.rolling(window=lookback, min_periods=lookback).mean()
    roll_std = dist.rolling(window=lookback, min_periods=lookback).std(ddof=0)

    with np.errstate(divide="ignore", invalid="ignore"):
        zscore = (dist - roll_mean) / roll_std

    zscore = zscore.where(roll_std > 0, other=np.nan)
    return zscore.rename("price_ma_zscore")
