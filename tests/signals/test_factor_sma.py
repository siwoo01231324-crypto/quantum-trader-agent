"""Tests for src/signals/sma.py — SMA + SMA crossover signal."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest


try:
    import pandas_ta as _pta  # noqa: F401

    _HAS_PTA = True
except Exception:
    _HAS_PTA = False


def test_sma_basic():
    from signals.sma import compute_sma

    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = compute_sma(s, window=3)
    # First 2 bars are NaN (warmup); then trailing means: 2, 3, 4
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[3] == pytest.approx(3.0)
    assert out.iloc[4] == pytest.approx(4.0)


def test_sma_length_matches_input():
    from signals.sma import compute_sma

    s = pd.Series(np.arange(50, dtype=float))
    assert len(compute_sma(s, window=10)) == 50


def test_sma_cross_golden_and_dead():
    """A down->up->down series produces both a golden and a dead cross."""
    from signals.sma import compute_sma_cross

    # Warmup with a downtrend so short SMA sits BELOW long SMA once both are valid,
    # then reverse to uptrend (short crosses above long = golden),
    # then back to downtrend (short crosses below long = dead).
    leg1 = np.linspace(200, 100, 40)  # down
    leg2 = np.linspace(100, 200, 40)  # up -> golden
    leg3 = np.linspace(200, 100, 40)  # down -> dead
    close = pd.Series(np.concatenate([leg1, leg2, leg3]))

    df = compute_sma_cross(close, short_window=5, long_window=20)
    assert set(df.columns) >= {"sma_short", "sma_long", "signal"}

    signals = df["signal"].dropna().unique()
    assert "golden" in signals, f"expected a golden cross, got {set(signals)}"
    assert "dead" in signals, f"expected a dead cross, got {set(signals)}"


def test_sma_cross_columns_no_unexpected():
    from signals.sma import compute_sma_cross

    close = pd.Series(np.arange(50, dtype=float))
    df = compute_sma_cross(close, short_window=3, long_window=10)
    allowed = {"sma_short", "sma_long", "signal"}
    assert set(df.columns) == allowed, f"unexpected columns: {set(df.columns) - allowed}"


@pytest.mark.skipif(not _HAS_PTA, reason="pandas-ta not installed")
def test_sma_matches_pandas_ta():
    import pandas_ta as pta
    from signals.sma import compute_sma

    np.random.seed(0)
    close = pd.Series(100 + np.cumsum(np.random.randn(100)))
    ours = compute_sma(close, window=14)
    theirs = pta.sma(close, length=14)
    pd.testing.assert_series_equal(
        ours.dropna().reset_index(drop=True),
        theirs.dropna().reset_index(drop=True),
        check_names=False,
        atol=1e-9,
    )
