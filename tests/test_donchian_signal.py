"""Unit tests for Donchian channel factor."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.signals.donchian import compute_donchian
from src.signals import compute, FACTOR_REGISTRY


def _make_series(values, name=None):
    idx = pd.date_range("2024-01-01", periods=len(values), freq="D")
    return pd.Series(values, index=idx, name=name, dtype=float)


class TestComputeDonchian:
    def test_known_values_window3(self):
        high = _make_series([1, 3, 2, 5, 4])
        low  = _make_series([0, 1, 1, 2, 2])
        result = compute_donchian(high, low, window=3)

        assert list(result.columns) == ["upper", "lower", "middle"]

        # First 2 rows are NaN (warmup = window-1)
        assert result["upper"].iloc[:2].isna().all()
        assert result["lower"].iloc[:2].isna().all()

        # row index 2: window over [0,1,2] -> upper=max(1,3,2)=3, lower=min(0,1,1)=0
        assert result["upper"].iloc[2] == pytest.approx(3.0)
        assert result["lower"].iloc[2] == pytest.approx(0.0)
        assert result["middle"].iloc[2] == pytest.approx(1.5)

        # row index 3: window over [1,2,3] -> upper=max(3,2,5)=5, lower=min(1,1,2)=1
        assert result["upper"].iloc[3] == pytest.approx(5.0)
        assert result["lower"].iloc[3] == pytest.approx(1.0)
        assert result["middle"].iloc[3] == pytest.approx(3.0)

        # row index 4: window over [2,3,4] -> upper=max(2,5,4)=5, lower=min(1,2,2)=1
        assert result["upper"].iloc[4] == pytest.approx(5.0)
        assert result["lower"].iloc[4] == pytest.approx(1.0)
        assert result["middle"].iloc[4] == pytest.approx(3.0)

    def test_default_window_is_20(self):
        n = 30
        high = _make_series(range(1, n + 1))
        low  = _make_series([0.0] * n)
        result = compute_donchian(high, low)

        # First 19 rows NaN, row 19 onwards has values
        assert result["upper"].iloc[:19].isna().all()
        assert not np.isnan(result["upper"].iloc[19])

    def test_window_parameter(self):
        n = 25
        high = _make_series(range(n, 0, -1))  # descending
        low  = _make_series(range(1, n + 1))   # ascending
        result = compute_donchian(high, low, window=10)

        assert result["upper"].iloc[:9].isna().all()
        assert not np.isnan(result["upper"].iloc[9])

    def test_middle_is_average_of_upper_lower(self):
        rng = np.random.default_rng(42)
        n = 50
        high = _make_series(rng.uniform(10, 20, n))
        low  = _make_series(rng.uniform(0, 10, n))
        result = compute_donchian(high, low, window=10)

        valid = result.dropna()
        expected_middle = (valid["upper"] + valid["lower"]) / 2.0
        pd.testing.assert_series_equal(valid["middle"], expected_middle, check_names=False)

    def test_upper_gte_lower(self):
        rng = np.random.default_rng(7)
        n = 40
        high = _make_series(rng.uniform(5, 15, n))
        low  = _make_series(rng.uniform(0, 5, n))
        result = compute_donchian(high, low, window=5)
        valid = result.dropna()
        assert (valid["upper"] >= valid["lower"]).all()

    def test_empty_series(self):
        high = pd.Series([], dtype=float)
        low  = pd.Series([], dtype=float)
        result = compute_donchian(high, low)
        assert len(result) == 0
        assert list(result.columns) == ["upper", "lower", "middle"]

    def test_series_shorter_than_window(self):
        high = _make_series([3.0, 4.0, 5.0])
        low  = _make_series([1.0, 2.0, 1.5])
        result = compute_donchian(high, low, window=10)
        assert result["upper"].isna().all()
        assert result["lower"].isna().all()

    def test_registered_in_registry(self):
        assert "donchian" in FACTOR_REGISTRY
        spec = FACTOR_REGISTRY["donchian"]
        assert spec.signal_type == "breakout"
        assert set(spec.inputs) == {"high", "low"}

    def test_compute_dispatch(self):
        high = _make_series([1, 3, 2, 5, 4])
        low  = _make_series([0, 1, 1, 2, 2])
        result = compute("donchian", high=high, low=low, window=3)
        assert isinstance(result, pd.DataFrame)
        assert "upper" in result.columns
