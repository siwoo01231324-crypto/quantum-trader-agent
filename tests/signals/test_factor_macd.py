"""Tests for src/signals/macd.py — MACD line, signal, histogram."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


try:
    import pandas_ta as _pta  # noqa: F401

    _HAS_PTA = True
except Exception:
    _HAS_PTA = False


def test_macd_returns_dataframe_columns():
    from signals.macd import compute_macd

    close = pd.Series(np.linspace(100, 200, 200))
    df = compute_macd(close)
    assert set(df.columns) == {"macd", "signal", "histogram"}
    assert len(df) == len(close)


def test_macd_histogram_is_diff():
    """histogram must equal macd - signal, element-wise."""
    from signals.macd import compute_macd

    np.random.seed(0)
    close = pd.Series(100 + np.cumsum(np.random.randn(300)))
    df = compute_macd(close)
    expected = df["macd"] - df["signal"]
    pd.testing.assert_series_equal(
        df["histogram"].dropna(),
        expected.dropna(),
        check_names=False,
        atol=1e-12,
    )


def test_macd_matches_manual_ewm():
    """Adjust=False EWMA of close; macd = ema(fast) - ema(slow); signal = ema(macd)."""
    from signals.macd import compute_macd

    np.random.seed(7)
    close = pd.Series(100 + np.cumsum(np.random.randn(200)))

    fast = close.ewm(span=12, adjust=False).mean()
    slow = close.ewm(span=26, adjust=False).mean()
    macd = fast - slow
    signal = macd.ewm(span=9, adjust=False).mean()

    df = compute_macd(close, fast=12, slow=26, signal=9)
    pd.testing.assert_series_equal(df["macd"], macd, check_names=False, atol=1e-12)
    pd.testing.assert_series_equal(df["signal"], signal, check_names=False, atol=1e-12)


@pytest.mark.skipif(not _HAS_PTA, reason="pandas-ta not installed")
def test_macd_matches_pandas_ta():
    import pandas_ta as pta
    from signals.macd import compute_macd

    np.random.seed(1)
    close = pd.Series(100 + np.cumsum(np.random.randn(300)))

    ours = compute_macd(close, fast=12, slow=26, signal=9)
    theirs = pta.macd(close, fast=12, slow=26, signal=9)
    # pandas-ta column order/name may differ; compare the macd line only
    ours_macd = ours["macd"].dropna().reset_index(drop=True)
    theirs_macd = theirs.iloc[:, 0].dropna().reset_index(drop=True)
    pd.testing.assert_series_equal(ours_macd, theirs_macd, check_names=False, atol=1e-6)
