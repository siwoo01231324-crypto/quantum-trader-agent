"""Volume-Weighted Moving Average (VWMA).

Reference: ``docs/background/36-vwma-volume-weighted-ma.md``.

VWMA_t(w) = sum(close[t-w+1:t+1] * volume[t-w+1:t+1])
            / sum(volume[t-w+1:t+1])

Reduces to SMA when volume is constant.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd


def vwma(
    close: pd.Series,
    volume: pd.Series,
    window: int = 100,
) -> pd.Series:
    """Rolling Volume-Weighted Moving Average.

    Parameters
    ----------
    close:
        Closing prices, indexed by bar timestamp.
    volume:
        Bar volume, same index as ``close``.
    window:
        Lookback in bars (default 100, per the Iranyi interview).

    Returns
    -------
    pd.Series
        VWMA values. Bars 0 .. window-2 are NaN. Bars where the rolling
        volume sum is zero return NaN with a warning issued.

    Raises
    ------
    ValueError
        If ``close`` and ``volume`` indices disagree, or if window < 1.
    """
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")
    if not close.index.equals(volume.index):
        raise ValueError("close and volume must share the same index")

    pv = (close * volume).rolling(window=window, min_periods=window).sum()
    v = volume.rolling(window=window, min_periods=window).sum()

    if (v == 0).any():
        warnings.warn(
            "vwma: rolling volume sum is zero on some bars — returning NaN",
            stacklevel=2,
        )

    with np.errstate(divide="ignore", invalid="ignore"):
        out = pv / v

    return out.rename("vwma")


def vwma_cross(
    close: pd.Series,
    volume: pd.Series,
    window: int = 100,
) -> pd.Series:
    """Causal cross signal between close and VWMA(window).

    Returns ``"golden"`` on the bar where close has just crossed above
    VWMA, ``"dead"`` on the bar where close has just crossed below, and
    ``None`` otherwise. Uses ``shift(1)`` on both close and VWMA so the
    signal at bar ``t`` is determined by data up to bar ``t-1``
    (lookahead-free).

    Parameters
    ----------
    close:
        Closing prices.
    volume:
        Bar volumes.
    window:
        VWMA window (default 100).

    Returns
    -------
    pd.Series
        Object dtype Series of ``"golden"`` / ``"dead"`` / ``None``.
    """
    line = vwma(close, volume, window=window)

    prev_close = close.shift(1)
    prev_line = line.shift(1)
    cur_close = close.shift(1)
    cur_line = line.shift(1)

    # The signal at bar t reflects a crossing observed by the close of
    # bar t-1 (vs t-2). This makes the signal usable for entries at
    # bar t's open without leaking the bar-t close.
    prev_close_2 = close.shift(2)
    prev_line_2 = line.shift(2)

    golden = (prev_close_2 < prev_line_2) & (cur_close > cur_line)
    dead = (prev_close_2 > prev_line_2) & (cur_close < cur_line)

    out = pd.Series([None] * len(close), index=close.index, dtype=object, name="vwma_cross")
    out[golden.fillna(False)] = "golden"
    out[dead.fillna(False)] = "dead"
    return out
