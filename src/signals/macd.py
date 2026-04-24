"""Moving Average Convergence Divergence (MACD)."""
from __future__ import annotations

import pandas as pd

from .registry import register


@register("macd", inputs=["close"], alpha_horizon_bars=10, bar_interval="1d", signal_type="momentum", fast=12, slow=26, signal=9)
def compute_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD line, signal line, histogram.

    Returns a DataFrame with columns ``macd``, ``signal``, ``histogram``.
    Uses ``ewm(adjust=False)`` so each EMA is recursive (industry convention).
    """
    fast_ema = close.ewm(span=fast, adjust=False).mean()
    slow_ema = close.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "signal": signal_line, "histogram": histogram},
        index=close.index,
    )
