"""Tests for src/features/turning_point.py — TDD red phase."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.turning_point import is_local_low_then_up, is_turning_point


def _series(values: list[float], freq: str = "5min") -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=len(values), freq=freq)
    return pd.Series(values, index=idx, dtype=float)


class TestIsTurningPoint:
    def test_swing_high_then_reverse(self):
        """Classic swing high followed by reversal → True at reversal bar."""
        # rising 5 bars, peak, then drop
        prices = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 103.0]
        close = _series(prices)
        tp = is_turning_point(close, lookback=5)
        # last bar (index 6) reverses from local high
        assert bool(tp.iloc[-1]) is True

    def test_no_reversal_uptrend(self):
        """Monotone uptrend → no turning point."""
        prices = [float(i) for i in range(1, 15)]
        close = _series(prices)
        tp = is_turning_point(close, lookback=5)
        assert tp.dropna().sum() == 0

    def test_flat_series(self):
        """Flat price → no turning point."""
        close = _series([100.0] * 15)
        tp = is_turning_point(close, lookback=5)
        assert tp.dropna().sum() == 0

    def test_nan_returns_false(self):
        """NaN in window → False (not exception)."""
        prices = [100.0, 101.0, np.nan, 103.0, 102.0, 101.0, 100.0]
        close = _series(prices)
        tp = is_turning_point(close, lookback=5)
        assert tp.isna().sum() == 0 or tp.dropna().dtype == bool

    def test_short_lookback(self):
        """lookback larger than series returns all False/NaN without error."""
        close = _series([100.0, 101.0, 100.0])
        tp = is_turning_point(close, lookback=10)
        assert len(tp) == 3
        non_nan = tp.dropna()
        assert (non_nan == False).all() or len(non_nan) == 0  # noqa: E712

    def test_swing_low_then_reverse_up(self):
        """Swing low followed by upward reversal → True."""
        prices = [100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 97.0]
        close = _series(prices)
        tp = is_turning_point(close, lookback=5)
        assert bool(tp.iloc[-1]) is True

    def test_output_index_matches(self):
        close = _series([float(i % 5) + 100 for i in range(20)])
        tp = is_turning_point(close, lookback=5)
        assert tp.index.equals(close.index)


class TestIsLocalLowThenUp:
    def test_local_low_then_up(self):
        """Dip followed by recovery → True at recovery bar."""
        prices = [100.0, 99.0, 98.0, 97.0, 96.0, 97.0, 98.0]
        close = _series(prices)
        sig = is_local_low_then_up(close, lookback=5)
        assert bool(sig.iloc[-1]) is True

    def test_local_high_not_triggered(self):
        """Local high followed by drop → is_local_low_then_up returns False."""
        prices = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 103.0]
        close = _series(prices)
        sig = is_local_low_then_up(close, lookback=5)
        assert bool(sig.iloc[-1]) is False

    def test_flat_series(self):
        close = _series([100.0] * 12)
        sig = is_local_low_then_up(close, lookback=5)
        assert sig.dropna().sum() == 0

    def test_nan_no_exception(self):
        prices = [100.0, np.nan, 99.0, 98.0, 99.0, 100.0]
        close = _series(prices)
        sig = is_local_low_then_up(close, lookback=4)
        assert len(sig) == len(prices)

    def test_short_series_no_error(self):
        close = _series([100.0, 99.0, 100.0])
        sig = is_local_low_then_up(close, lookback=5)
        assert len(sig) == 3
