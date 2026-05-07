"""Tests for src/features/volume_burst.py — TDD red phase."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.volume_burst import volume_burst_signal, volume_zscore


def _make_volume(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


class TestVolumeZscore:
    def test_normal_burst(self):
        """A single large spike should yield a high z-score at that bar."""
        vol = _make_volume([100.0] * 20 + [1000.0])
        z = volume_zscore(vol, lookback=20)
        assert z.iloc[-1] > 2.0

    def test_flat_volume_zero_std(self):
        """Constant volume → std=0 → z-score should be 0 (not NaN/inf)."""
        vol = _make_volume([100.0] * 30)
        z = volume_zscore(vol, lookback=20)
        finite = z.dropna()
        assert (finite == 0.0).all()

    def test_monotone_increase(self):
        """Steadily rising volume: z-scores should be finite, mostly positive."""
        vol = _make_volume(list(range(1, 41)))
        z = volume_zscore(vol, lookback=20)
        finite = z.dropna()
        assert len(finite) > 0
        assert np.isfinite(finite.values).all()

    def test_nan_propagation(self):
        """NaN in input window → NaN output at that bar."""
        values = [100.0] * 10 + [np.nan] + [100.0] * 10
        vol = _make_volume(values)
        z = volume_zscore(vol, lookback=5)
        # bar at index 10 (the NaN bar) should propagate NaN
        assert np.isnan(z.iloc[10])

    def test_short_lookback(self):
        """lookback=2 should produce non-NaN values after bar 1."""
        vol = _make_volume([10.0, 20.0, 10.0, 20.0, 10.0])
        z = volume_zscore(vol, lookback=2)
        assert z.notna().sum() >= 3

    def test_output_index_matches_input(self):
        vol = _make_volume([float(i) for i in range(1, 31)])
        z = volume_zscore(vol, lookback=10)
        assert z.index.equals(vol.index)

    def test_lookback_gt_series_len(self):
        """If lookback > len(series), all values should be NaN."""
        vol = _make_volume([10.0, 20.0, 30.0])
        z = volume_zscore(vol, lookback=10)
        assert z.isna().all()


class TestVolumeBurstSignal:
    def test_burst_detected(self):
        """Spike bar should be True when z > z_threshold."""
        vol = _make_volume([100.0] * 20 + [5000.0])
        sig = volume_burst_signal(vol, lookback=20, z_threshold=2.0)
        assert bool(sig.iloc[-1]) is True

    def test_flat_not_burst(self):
        """Constant volume → no burst signal."""
        vol = _make_volume([100.0] * 30)
        sig = volume_burst_signal(vol, lookback=20, z_threshold=2.0)
        assert sig.dropna().sum() == 0

    def test_output_is_boolean_series(self):
        vol = _make_volume([float(i % 5 + 1) * 100 for i in range(30)])
        sig = volume_burst_signal(vol, lookback=10, z_threshold=1.5)
        assert sig.dtype == bool or sig.dtype == object
        assert isinstance(sig, pd.Series)

    def test_nan_rows_are_false_or_nan(self):
        """NaN input bars should not yield True burst signal."""
        values = [100.0] * 10 + [np.nan] + [100.0] * 10
        vol = _make_volume(values)
        sig = volume_burst_signal(vol, lookback=5, z_threshold=2.0)
        # NaN bar should not be True
        assert sig.iloc[10] is not True and not (
            isinstance(sig.iloc[10], float) and sig.iloc[10] == 1.0
        )

    def test_threshold_boundary(self):
        """z exactly at threshold should be False (strict >)."""
        vol = _make_volume([100.0] * 20 + [1000.0])
        z = volume_zscore(vol, lookback=20)
        threshold = float(z.iloc[-1])
        sig = volume_burst_signal(vol, lookback=20, z_threshold=threshold)
        assert bool(sig.iloc[-1]) is False

    def test_index_preserved(self):
        idx = pd.date_range("2024-01-01", periods=25, freq="5min")
        vol = pd.Series([100.0] * 24 + [9999.0], index=idx)
        sig = volume_burst_signal(vol, lookback=20)
        assert sig.index.equals(idx)
