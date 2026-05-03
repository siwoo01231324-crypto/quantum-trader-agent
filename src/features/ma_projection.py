"""Forward EMA projection features.

Reference: ``docs/background/41-multi-tf-fractal-trading.md`` §4.4.

Computes EMA slope, curvature, and a forward extrapolation of the EMA
to a horizon of ``N`` bars. The features are causal (they only use the
EMA at and before bar ``t``).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False, min_periods=span).mean()


def ema_slope(
    close: pd.Series,
    span: int = 100,
    slope_window: int = 5,
) -> pd.Series:
    """Linear-regression slope of the EMA over the last ``slope_window`` bars.

    Returns
    -------
    pd.Series
        Slope per bar, in EMA-units per bar. Positive => EMA rising.
        First ``span + slope_window - 2`` bars are NaN.
    """
    line = _ema(close, span)
    x = np.arange(slope_window, dtype=float)
    x_centered = x - x.mean()
    denom = float((x_centered ** 2).sum())

    def _slope(window_vals: "np.ndarray") -> float:
        y = window_vals - window_vals.mean()
        return float((x_centered * y).sum() / denom) if denom > 0 else 0.0

    return line.rolling(window=slope_window, min_periods=slope_window).apply(
        _slope, raw=True
    ).rename("ema_slope")


def ema_curvature(
    close: pd.Series,
    span: int = 100,
    slope_window: int = 5,
) -> pd.Series:
    """Slope of slope (second-derivative proxy).

    Returns
    -------
    pd.Series
        Second derivative per bar. Positive => EMA accelerating upward.
    """
    s = ema_slope(close, span=span, slope_window=slope_window)
    x = np.arange(slope_window, dtype=float)
    x_centered = x - x.mean()
    denom = float((x_centered ** 2).sum())

    def _slope(window_vals: "np.ndarray") -> float:
        y = window_vals - window_vals.mean()
        return float((x_centered * y).sum() / denom) if denom > 0 else 0.0

    return s.rolling(window=slope_window, min_periods=slope_window).apply(
        _slope, raw=True
    ).rename("ema_curvature")


def ema_projection(
    close: pd.Series,
    span: int = 100,
    horizon: int = 10,
    slope_window: int = 5,
) -> pd.DataFrame:
    """Forward EMA projection via linear extrapolation.

    Parameters
    ----------
    close:
        Closing prices.
    span:
        EMA span.
    horizon:
        Forward horizon (in bars) to project.
    slope_window:
        Bars used for the slope estimate.

    Returns
    -------
    pd.DataFrame with columns:
        - ``ema_proj_n``: projected EMA value at t+horizon
        - ``eta_to_cross``: estimated bars until close crosses the
          projected EMA path (``np.inf`` when paths diverge or are
          parallel)
        - ``price_to_ema_gap_at_n``: projected gap between (close held
          flat) and EMA at t+horizon
    """
    line = _ema(close, span)
    s = ema_slope(close, span=span, slope_window=slope_window)

    # Linear extrapolation of EMA to t+horizon
    ema_proj_n = line + s * horizon

    # Approximate the close-vs-EMA crossing time assuming close is flat
    # and EMA continues with current slope. ``s == 0`` means parallel
    # (no crossing); ``(close - line) / s < 0`` means diverging.
    gap = close - line
    with np.errstate(divide="ignore", invalid="ignore"):
        eta = -gap / s
    eta = eta.where(s != 0, other=np.inf)
    eta = eta.where(eta >= 0, other=np.inf)
    eta = eta.rename("eta_to_cross")

    price_to_ema_gap_at_n = (close - ema_proj_n).rename("price_to_ema_gap_at_n")

    return pd.DataFrame(
        {
            "ema_proj_n": ema_proj_n.rename("ema_proj_n"),
            "eta_to_cross": eta,
            "price_to_ema_gap_at_n": price_to_ema_gap_at_n,
        }
    )
