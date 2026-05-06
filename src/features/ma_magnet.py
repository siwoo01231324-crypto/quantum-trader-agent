"""MA200 magnet features (Iranyi rule #7, issue #185).

Rolling z-score of (close - MA200) distance and a signal that fires when
price is far below MA200 (mean-reversion / magnet setup).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ma200_distance_zscore(
    close: pd.Series,
    ma200: pd.Series,
    window: int = 200,
) -> pd.Series:
    """Rolling z-score of (close - MA200) over `window` bars.

    Parameters
    ----------
    close:
        Closing prices.
    ma200:
        200-bar moving average of close.
    window:
        Lookback for rolling mean and std of the distance.

    Returns
    -------
    pd.Series
        Z-score series; NaN during warmup or when rolling std is zero.
    """
    dist = close - ma200
    roll_mean = dist.rolling(window=window, min_periods=window).mean()
    roll_std = dist.rolling(window=window, min_periods=window).std(ddof=0)

    with np.errstate(divide="ignore", invalid="ignore"):
        zscore = (dist - roll_mean) / roll_std

    # Zero std → z-score is 0 (constant distance) but return NaN to flag it
    zscore = zscore.where(roll_std > 0, other=np.nan)
    return zscore.rename("ma200_distance_zscore")


def return_to_ma_signal(
    close: pd.Series,
    ma200: pd.Series,
    z_threshold: float = -1.5,
) -> pd.Series:
    """Boolean signal: True when close is far below MA200 (mean-reversion setup).

    Parameters
    ----------
    close:
        Closing prices.
    ma200:
        200-bar moving average of close.
    z_threshold:
        Z-score threshold below which the signal fires (typically negative).

    Returns
    -------
    pd.Series
        Boolean series; False during warmup.
    """
    zscore = ma200_distance_zscore(close, ma200)
    signal = zscore < z_threshold
    return signal.fillna(False).rename("return_to_ma_signal")
