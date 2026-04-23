"""Tests for src/signals/realized_vol.py — rolling realized volatility."""
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


def test_realized_vol_formula():
    from signals.realized_vol import compute_realized_vol

    np.random.seed(0)
    close = pd.Series(100 + np.cumsum(np.random.randn(80)))
    rv = compute_realized_vol(close, window=20, annualize=252)

    expected_logret = np.log(close / close.shift(1))
    expected_rv = expected_logret.rolling(20).std() * math.sqrt(252)

    pd.testing.assert_series_equal(
        rv.dropna().reset_index(drop=True),
        expected_rv.dropna().reset_index(drop=True),
        check_names=False,
        atol=1e-12,
    )


def test_realized_vol_annualization_scales():
    """Switching annualize factor scales output by sqrt(ratio)."""
    from signals.realized_vol import compute_realized_vol

    np.random.seed(1)
    close = pd.Series(100 + np.cumsum(np.random.randn(80)))
    rv_252 = compute_realized_vol(close, window=20, annualize=252)
    rv_365 = compute_realized_vol(close, window=20, annualize=365)

    ratio = (rv_365 / rv_252).dropna()
    assert np.allclose(ratio, math.sqrt(365 / 252), atol=1e-10)


def test_realized_vol_length_matches_input():
    from signals.realized_vol import compute_realized_vol

    close = pd.Series(np.arange(1, 51, dtype=float))
    assert len(compute_realized_vol(close, window=10)) == 50


@pytest.mark.skipif(not _HAS_PTA, reason="pandas-ta not installed")
def test_realized_vol_matches_pandas_ta_stdev():
    """Sanity: our rv / sqrt(annualize) must match pandas-ta stdev of log returns."""
    import pandas_ta as pta
    from signals.realized_vol import compute_realized_vol

    np.random.seed(2)
    close = pd.Series(100 + np.cumsum(np.random.randn(200)))

    ours = compute_realized_vol(close, window=20, annualize=1)  # un-annualized
    log_ret = np.log(close / close.shift(1))
    theirs = pta.stdev(log_ret, length=20)

    pd.testing.assert_series_equal(
        ours.dropna().reset_index(drop=True),
        theirs.dropna().reset_index(drop=True),
        check_names=False,
        atol=1e-9,
    )
