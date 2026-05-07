"""Forward MA projection meeting point (Iranyi rule #3, issue #185).

Linear extrapolation of the last `horizon` bars of each MA series to
estimate how many bars until they meet and at what projected price.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _linear_slope(series: pd.Series, horizon: int) -> pd.Series:
    """Rolling linear-regression slope over `horizon` bars."""
    x = np.arange(horizon, dtype=float)
    x_c = x - x.mean()
    denom = float((x_c ** 2).sum())

    def _slope(w: np.ndarray) -> float:
        if denom == 0 or np.isnan(w).any():
            return np.nan
        y_c = w - w.mean()
        return float((x_c * y_c).sum() / denom)

    return series.rolling(window=horizon, min_periods=horizon).apply(_slope, raw=True)


def ma_projection_meeting_point(
    vwma_series: pd.Series,
    ma_series: pd.Series,
    horizon: int = 20,
) -> pd.DataFrame:
    """Estimate when and where VWMA and MA will meet via linear extrapolation.

    Parameters
    ----------
    vwma_series:
        VWMA (or any first MA) values.
    ma_series:
        Second MA values (e.g. EMA or SMA).
    horizon:
        Bars used for slope estimation and forward projection.

    Returns
    -------
    pd.DataFrame with columns:
        - ``bars_to_meet``: estimated bars until the two projected lines meet
          (np.inf when parallel or diverging).
        - ``projected_price``: estimated price at the meeting point.
    """
    slope_vwma = _linear_slope(vwma_series, horizon)
    slope_ma = _linear_slope(ma_series, horizon)

    # gap = vwma - ma at current bar; positive means vwma above ma
    gap = vwma_series - ma_series

    # relative slope delta: how fast gap is closing
    d_slope = slope_vwma - slope_ma

    with np.errstate(divide="ignore", invalid="ignore"):
        bars_to_meet = -gap / d_slope

    # Only finite positive values are meaningful
    bars_to_meet = bars_to_meet.where(
        (d_slope != 0) & (~d_slope.isna()) & (~gap.isna()),
        other=np.inf,
    )
    bars_to_meet = bars_to_meet.where(bars_to_meet > 0, other=np.inf)

    # projected price: use vwma linear projection at the meeting bar
    projected_price = vwma_series + slope_vwma * bars_to_meet
    # When bars_to_meet is inf, projected_price should be NaN
    projected_price = projected_price.where(bars_to_meet < np.inf, other=np.nan)

    return pd.DataFrame(
        {
            "bars_to_meet": bars_to_meet,
            "projected_price": projected_price,
        },
        index=vwma_series.index,
    )
