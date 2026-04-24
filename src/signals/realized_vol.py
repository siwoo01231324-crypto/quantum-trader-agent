"""Rolling realized volatility (log-return std, annualized)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .registry import register


@register("realized_vol", inputs=["close"], alpha_horizon_bars=20, bar_interval="1d", signal_type="volatility", window=20, annualize=252)
def compute_realized_vol(
    close: pd.Series,
    window: int = 20,
    annualize: int = 252,
) -> pd.Series:
    """sigma = rolling_std(log_returns, window) * sqrt(annualize).

    ``annualize=252`` for equity, ``365`` for 24/7 crypto, ``1`` for un-annualized.
    """
    log_returns = np.log(close / close.shift(1))
    return log_returns.rolling(window).std() * math.sqrt(annualize)
