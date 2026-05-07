"""Tests for src/features/vpvr_poc.py — TDD red phase."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.vpvr_poc import near_support_zone, volume_profile_support_zones


def _make_ohlcv(n: int = 50, base: float = 100.0) -> pd.DataFrame:
    np.random.seed(42)
    close = base + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    open_ = close - np.random.randn(n) * 0.2
    volume = np.random.uniform(100, 1000, n)
    idx = pd.date_range("2024-01-01", periods=n, freq="5min")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


class TestVolumeProfileSupportZones:
    def test_single_dominant_zone(self):
        """Heavy volume at specific price range → POC should be within that range."""
        n = 60
        idx = pd.date_range("2024-01-01", periods=n, freq="5min")
        close = np.concatenate([np.full(30, 100.0), np.full(30, 110.0)])
        volume = np.concatenate([np.full(30, 10.0), np.full(30, 1000.0)])
        df = pd.DataFrame(
            {
                "open": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": volume,
            },
            index=idx,
        )
        result = volume_profile_support_zones(df, window=30, n_bins=10, top_k=3)
        # POC at last bar should be near 110
        assert result["poc_price"].iloc[-1] > 105.0

    def test_multiple_support_zones_columns(self):
        """Result DataFrame should have poc_price + top_k zone columns."""
        df = _make_ohlcv(80)
        result = volume_profile_support_zones(df, window=40, n_bins=24, top_k=3)
        assert "poc_price" in result.columns
        assert "zone_1" in result.columns
        assert "zone_2" in result.columns
        assert "zone_3" in result.columns

    def test_empty_window_before_warmup(self):
        """Bars before window-1 should have NaN."""
        df = _make_ohlcv(50)
        result = volume_profile_support_zones(df, window=30, n_bins=10, top_k=2)
        assert result["poc_price"].iloc[:29].isna().all()

    def test_nan_ohlcv_handled(self):
        """NaN in OHLCV should not raise, just yield NaN output for that window."""
        df = _make_ohlcv(50)
        df.loc[df.index[10], "close"] = np.nan
        df.loc[df.index[10], "volume"] = np.nan
        result = volume_profile_support_zones(df, window=20, n_bins=10, top_k=2)
        assert len(result) == len(df)

    def test_small_n_bins(self):
        """n_bins=2 should still work without error."""
        df = _make_ohlcv(30)
        result = volume_profile_support_zones(df, window=10, n_bins=2, top_k=1)
        assert "poc_price" in result.columns
        assert len(result) == len(df)

    def test_reuses_poc_not_duplicate_logic(self):
        """vpvr_poc must import from src.features.poc — verify by checking module source."""
        import inspect

        import src.features.vpvr_poc as mod

        source = inspect.getsource(mod)
        assert "from src.features.poc import" in source or "from .poc import" in source


class TestNearSupportZone:
    def test_close_to_zone(self):
        """close within tolerance of a zone → True."""
        idx = pd.date_range("2024-01-01", periods=5, freq="5min")
        close = pd.Series([100.0, 100.1, 99.9, 100.05, 100.0], index=idx)
        support_df = pd.DataFrame(
            {"poc_price": [100.0] * 5, "zone_1": [100.0] * 5, "zone_2": [105.0] * 5},
            index=idx,
        )
        result = near_support_zone(close, support_df, tolerance_pct=0.005)
        assert result.iloc[0] is True or bool(result.iloc[0]) is True

    def test_far_from_zone(self):
        """close far from all zones → False."""
        idx = pd.date_range("2024-01-01", periods=3, freq="5min")
        close = pd.Series([200.0, 200.0, 200.0], index=idx)
        support_df = pd.DataFrame(
            {"poc_price": [100.0] * 3, "zone_1": [100.0] * 3},
            index=idx,
        )
        result = near_support_zone(close, support_df, tolerance_pct=0.005)
        assert result.sum() == 0

    def test_output_boolean_series(self):
        df = _make_ohlcv(50)
        zones = volume_profile_support_zones(df, window=20, n_bins=10, top_k=2)
        result = near_support_zone(df["close"], zones, tolerance_pct=0.01)
        assert isinstance(result, pd.Series)
        valid = result.dropna()
        assert set(valid.unique()).issubset({True, False})
