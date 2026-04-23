"""Tests for src/signals/atr.py — Wilder-smoothed Average True Range."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


try:
    import pandas_ta as _pta  # noqa: F401

    _HAS_PTA = True
except Exception:
    _HAS_PTA = False


def test_atr_first_n_nan():
    from signals.atr import compute_atr

    np.random.seed(0)
    n = 30
    close = pd.Series(100 + np.cumsum(np.random.randn(n)))
    high = close + np.random.rand(n)
    low = close - np.random.rand(n)

    atr = compute_atr(high, low, close, window=14)
    # Warmup: bars 0..13 inclusive should be NaN (first valid ATR at index 14)
    assert atr.iloc[:14].isna().all()
    assert not atr.iloc[14:].isna().any()


def test_atr_manual_value():
    """Hand-computed TR and Wilder smoothing on a short series.

    TR_i = max(H_i - L_i, |H_i - C_{i-1}|, |L_i - C_{i-1}|) for i>=1.
    ATR_14 at bar 14 = mean(TR_1 .. TR_14). Subsequent bars use
    ATR_i = (ATR_{i-1} * 13 + TR_i) / 14.
    """
    from signals.atr import compute_atr

    # Simple constant series: H = C + 1, L = C - 1, C increases by 1 each bar
    n = 20
    close = pd.Series(np.arange(100, 100 + n, dtype=float))
    high = close + 1.0
    low = close - 1.0

    atr = compute_atr(high, low, close, window=14)
    # Each TR_i (i>=1) = max(H-L=2, |H-C_prev|=2, |L-C_prev|=0) = 2
    # Seed ATR at i=14 = mean of TR_1..TR_14 = 2.0
    # Subsequent bars keep ATR = 2.0 since TR stays 2.0
    assert atr.iloc[14] == pytest.approx(2.0)
    assert atr.iloc[-1] == pytest.approx(2.0)


def test_atr_length_matches_input():
    from signals.atr import compute_atr

    n = 50
    close = pd.Series(np.arange(n, dtype=float))
    high = close + 1
    low = close - 1
    assert len(compute_atr(high, low, close, window=14)) == n


@pytest.mark.skipif(not _HAS_PTA, reason="pandas-ta not installed")
def test_atr_matches_pandas_ta():
    import pandas_ta as pta
    from signals.atr import compute_atr

    np.random.seed(1)
    n = 200
    close = pd.Series(100 + np.cumsum(np.random.randn(n)))
    high = close + np.random.rand(n)
    low = close - np.random.rand(n)

    ours = compute_atr(high, low, close, window=14)
    theirs = pta.atr(high, low, close, length=14, mamode="rma")
    pd.testing.assert_series_equal(
        ours.dropna().reset_index(drop=True),
        theirs.dropna().reset_index(drop=True),
        check_names=False,
        atol=1e-6,
    )
