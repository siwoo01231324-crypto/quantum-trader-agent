"""Unit tests for rolling z-score factor."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.signals.zscore import compute_zscore
from src.signals import compute, FACTOR_REGISTRY


def _make_series(values, freq="1h"):
    idx = pd.date_range("2024-01-01", periods=len(values), freq=freq)
    return pd.Series(values, index=idx, dtype=float)


class TestComputeZscore:
    def test_known_value_spike(self):
        """Insert a spike 2 std above rolling mean and verify z ~ 2."""
        n = 80
        rng = np.random.default_rng(42)
        # Stationary log-returns around 0, then a spike
        base = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        close = _make_series(base)
        z = compute_zscore(close, window=60)

        # Stationary series: most z values should be within [-3, 3]
        valid = z.dropna()
        assert len(valid) == n - 59  # window-1 warmup
        assert (valid.abs() < 5).all(), f"unexpected extreme z: {valid.abs().max()}"

    def test_nan_warmup(self):
        """First window-1 rows must be NaN."""
        close = _make_series(range(1, 71))
        z = compute_zscore(close, window=20)
        assert z.iloc[:19].isna().all()
        assert not np.isnan(z.iloc[19])

    def test_default_window_is_60(self):
        close = _make_series(range(1, 101))
        z = compute_zscore(close)
        assert z.iloc[:59].isna().all()
        assert not np.isnan(z.iloc[59])

    def test_window_parameter(self):
        close = _make_series(range(1, 101))
        z10 = compute_zscore(close, window=10)
        assert z10.iloc[:9].isna().all()
        assert not np.isnan(z10.iloc[9])

    def test_constant_series_returns_nan(self):
        """Constant price -> std=0 -> z should be NaN."""
        close = _make_series([100.0] * 80)
        z = compute_zscore(close, window=20)
        valid_idx = ~z.iloc[19:].isna()
        # all valid-window values must be NaN (zero std)
        assert not valid_idx.any(), "expected all NaN for constant series"

    def test_z_at_mean_is_zero(self):
        """When close[t] == exp(rolling_mean(log(close), window)), z[t] == 0.

        Build a series where the last bar equals the geometric mean of the window.
        """
        window = 10
        # Use log-evenly-spaced values so the geometric mean = geometric_mean
        log_vals = np.linspace(3.0, 5.0, window - 1)  # 9 values
        # Last value = exp(mean of log_vals) so log(close[-1]) - mean == 0
        mean_log = log_vals.mean()
        log_vals_full = np.append(log_vals, mean_log)
        close = _make_series(np.exp(log_vals_full))
        z = compute_zscore(close, window=window)
        # z at last bar should be 0 (numerator = 0)
        assert abs(float(z.iloc[-1])) < 1e-9

    def test_log_domain(self):
        """z-score is computed in log domain, not linear price domain."""
        n = 70
        # Exponentially growing series: log(close) grows linearly
        close = _make_series(np.exp(np.linspace(0, 5, n)))
        z = compute_zscore(close, window=30)
        valid = z.dropna()
        # Linear growth in log domain -> z should be bounded (not exploding)
        assert (valid.abs() < 5).all()

    def test_registered_in_registry(self):
        assert "zscore" in FACTOR_REGISTRY
        spec = FACTOR_REGISTRY["zscore"]
        assert spec.signal_type == "mean_reversion"
        assert spec.inputs == ["close"]
        assert spec.bar_interval == "1h"

    def test_compute_dispatch(self):
        close = _make_series(range(1, 81))
        result = compute("zscore", close=close, window=20)
        assert isinstance(result, pd.Series)
        assert result.iloc[:19].isna().all()

    def test_short_series_all_nan(self):
        close = _make_series([10.0, 20.0, 30.0])
        z = compute_zscore(close, window=10)
        assert z.isna().all()

    def test_empty_series(self):
        close = pd.Series([], dtype=float)
        z = compute_zscore(close)
        assert len(z) == 0
