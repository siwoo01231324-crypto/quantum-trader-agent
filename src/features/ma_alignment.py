"""MA alignment pre-cross feature (Iranyi rule #2, issue #185).

True at bar t when MA_short crossed above MA_long within the last `lookback`
bars AND both MAs are trending upward (positive slope).
"""
from __future__ import annotations

import pandas as pd


def ma_aligned_pre_cross(
    close: pd.Series,
    period_short: int = 50,
    period_long: int = 100,
    lookback: int = 10,
) -> pd.Series:
    """True when short MA crossed above long MA within recent `lookback` bars.

    Parameters
    ----------
    close:
        Closing prices.
    period_short:
        Short MA window.
    period_long:
        Long MA window (must be > period_short).
    lookback:
        Number of bars to look back for the cross event.

    Returns
    -------
    pd.Series
        Boolean series, same index as ``close``. False during warmup.
    """
    ma_short = close.rolling(window=period_short, min_periods=period_short).mean()
    ma_long = close.rolling(window=period_long, min_periods=period_long).mean()

    # cross_up[t] = True if ma_short[t-1] <= ma_long[t-1] and ma_short[t] > ma_long[t]
    above = (ma_short > ma_long).fillna(False)
    was_below_or_equal = (ma_short.shift(1) <= ma_long.shift(1)).fillna(False)
    cross_up = above & was_below_or_equal

    # rolling window: True if any cross_up in last lookback bars (inclusive)
    recent_cross = (
        cross_up.astype(float)
        .rolling(window=lookback, min_periods=lookback)
        .max()
        .fillna(0.0)
        .astype(bool)
    )

    # Both MAs must slope upward (positive diff over last 2 bars)
    short_slope_up = (ma_short - ma_short.shift(1)) > 0
    long_slope_up = (ma_long - ma_long.shift(1)) > 0

    result = recent_cross & short_slope_up & long_slope_up
    return result.fillna(False)
