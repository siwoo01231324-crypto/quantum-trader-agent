import pandas as pd
import numpy as np

from .registry import register


@register("rsi", inputs=["close"], alpha_horizon_bars=5, bar_interval="1d", signal_type="mean_reversion", period=14)
def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder-smoothed RSI using true Wilder smoothing (SMMA).

    The first average is a simple mean over the first `period` bars.
    Subsequent values use Wilder's smoothing: avg = prev_avg * (period-1)/period + current/period.
    First `period` values are NaN.
    """
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    rsi = pd.Series(np.nan, index=close.index, dtype=float)

    # Seed the first average with the SMA of the first `period` bars
    # delta.diff() shifts by 1, so bars 1..period (index 1..period) form the seed window
    if len(gain) < period + 1:
        return rsi

    # Seed: SMA of gains/losses over first `period` deltas (indices 1..period)
    avg_gain = gain.iloc[1 : period + 1].mean()
    avg_loss = loss.iloc[1 : period + 1].mean()

    # First valid RSI is at index `period`
    if avg_loss == 0:
        rsi.iloc[period] = 100.0
    else:
        rsi.iloc[period] = 100 - 100 / (1 + avg_gain / avg_loss)

    for i in range(period + 1, len(close)):
        avg_gain = (avg_gain * (period - 1) + gain.iloc[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss.iloc[i]) / period
        if avg_loss == 0:
            rsi.iloc[i] = 100.0
        else:
            rsi.iloc[i] = 100 - 100 / (1 + avg_gain / avg_loss)

    return rsi


def detect_divergence(
    close: pd.Series, rsi: pd.Series, lookback: int = 14
) -> pd.Series:
    """Rolling min/max divergence detection with lag-1 to prevent lookahead.

    Algorithm:
    1. Shift close and rsi by 1 (lag-1).
    2. Compute rolling min/max over `lookback` bars on shifted series.
    3. Compare current window min/max against the previous window (shift by lookback).
    4. Bullish divergence: price lower low, RSI higher low.
       Bearish divergence: price higher high, RSI lower high.
    5. If both conditions true, prioritize bearish (exit is safer than entry).

    Returns a Series of 'bullish', 'bearish', or None.
    """
    shifted_close = close.shift(1)
    shifted_rsi = rsi.shift(1)

    price_low = shifted_close.rolling(lookback).min()
    rsi_low = shifted_rsi.rolling(lookback).min()
    price_high = shifted_close.rolling(lookback).max()
    rsi_high = shifted_rsi.rolling(lookback).max()

    prev_price_low = price_low.shift(lookback)
    prev_rsi_low = rsi_low.shift(lookback)
    prev_price_high = price_high.shift(lookback)
    prev_rsi_high = rsi_high.shift(lookback)

    bullish = (price_low < prev_price_low) & (rsi_low > prev_rsi_low)
    bearish = (price_high > prev_price_high) & (rsi_high < prev_rsi_high)

    result = pd.Series([None] * len(close), index=close.index, dtype=object)
    result[bullish] = "bullish"
    result[bearish] = "bearish"  # bearish overwrites bullish if both true
    return result
