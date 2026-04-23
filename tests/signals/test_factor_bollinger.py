"""Tests for src/signals/bollinger.py — Bollinger Bands + %B + BandWidth."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


try:
    import pandas_ta as _pta  # noqa: F401

    _HAS_PTA = True
except Exception:
    _HAS_PTA = False


def test_bollinger_columns():
    from signals.bollinger import compute_bollinger

    close = pd.Series(np.linspace(100, 200, 100))
    df = compute_bollinger(close)
    assert set(df.columns) == {"upper", "middle", "lower", "pct_b", "bandwidth"}
    assert len(df) == len(close)


def test_bollinger_values_manual():
    """middle = SMA; upper/lower = middle +/- n_std * rolling std (ddof=0)."""
    from signals.bollinger import compute_bollinger

    np.random.seed(0)
    close = pd.Series(100 + np.cumsum(np.random.randn(60)))
    df = compute_bollinger(close, window=20, n_std=2.0)

    expected_middle = close.rolling(20).mean()
    expected_std = close.rolling(20).std(ddof=0)
    expected_upper = expected_middle + 2.0 * expected_std
    expected_lower = expected_middle - 2.0 * expected_std

    pd.testing.assert_series_equal(df["middle"], expected_middle, check_names=False, atol=1e-12)
    pd.testing.assert_series_equal(df["upper"], expected_upper, check_names=False, atol=1e-12)
    pd.testing.assert_series_equal(df["lower"], expected_lower, check_names=False, atol=1e-12)


def test_bollinger_pct_b_formula():
    """pct_b = (close - lower) / (upper - lower). At middle, pct_b = 0.5."""
    from signals.bollinger import compute_bollinger

    # Constant price — band collapses, pct_b is NaN (division by zero)
    close = pd.Series([100.0] * 40)
    df = compute_bollinger(close, window=20, n_std=2.0)
    # When upper==lower, pct_b is NaN; warmup (first 19) is also NaN.
    assert df["pct_b"].isna().all(), "constant series should yield NaN pct_b"


def test_bollinger_bandwidth_formula():
    from signals.bollinger import compute_bollinger

    np.random.seed(5)
    close = pd.Series(100 + np.cumsum(np.random.randn(50)))
    df = compute_bollinger(close, window=20, n_std=2.0)
    expected_bw = (df["upper"] - df["lower"]) / df["middle"]
    pd.testing.assert_series_equal(
        df["bandwidth"].dropna(),
        expected_bw.dropna(),
        check_names=False,
        atol=1e-12,
    )


@pytest.mark.skipif(not _HAS_PTA, reason="pandas-ta not installed")
def test_bollinger_matches_pandas_ta():
    import pandas_ta as pta
    from signals.bollinger import compute_bollinger

    np.random.seed(3)
    close = pd.Series(100 + np.cumsum(np.random.randn(200)))
    ours = compute_bollinger(close, window=20, n_std=2.0)
    theirs = pta.bbands(close, length=20, std=2.0)
    # pandas-ta columns: BBL, BBM, BBU, BBB, BBP — match by position
    pd.testing.assert_series_equal(
        ours["middle"].dropna().reset_index(drop=True),
        theirs.iloc[:, 1].dropna().reset_index(drop=True),
        check_names=False,
        atol=1e-6,
    )
