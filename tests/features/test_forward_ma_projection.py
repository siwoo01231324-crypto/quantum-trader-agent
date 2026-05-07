"""Tests for src/features/forward_ma_projection.py (issue #185 W1)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.forward_ma_projection import ma_projection_meeting_point


def _make_idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")


class TestMaProjectionMeetingPoint:
    def test_returns_dataframe_with_correct_columns(self) -> None:
        idx = _make_idx(150)
        vwma = pd.Series(np.linspace(100.0, 120.0, 150), index=idx)
        ma = pd.Series(np.linspace(105.0, 118.0, 150), index=idx)
        out = ma_projection_meeting_point(vwma, ma, horizon=20)
        assert isinstance(out, pd.DataFrame)
        assert "bars_to_meet" in out.columns
        assert "projected_price" in out.columns

    def test_output_length_matches_input(self) -> None:
        idx = _make_idx(150)
        vwma = pd.Series(np.linspace(100.0, 130.0, 150), index=idx)
        ma = pd.Series(np.linspace(95.0, 125.0, 150), index=idx)
        out = ma_projection_meeting_point(vwma, ma, horizon=20)
        assert len(out) == 150

    def test_parallel_lines_never_meet(self) -> None:
        """Parallel (same slope) series should give inf bars_to_meet."""
        idx = _make_idx(150)
        slope = np.arange(150, dtype=float)
        vwma = pd.Series(slope + 10.0, index=idx)
        ma = pd.Series(slope + 20.0, index=idx)  # always 10 apart, same slope
        out = ma_projection_meeting_point(vwma, ma, horizon=20)
        valid = out["bars_to_meet"].dropna()
        assert (valid == np.inf).all(), "Parallel lines should never meet"

    def test_converging_lines_gives_finite_bars(self) -> None:
        """Lines converging toward each other should give finite bars_to_meet."""
        n = 200
        idx = _make_idx(n)
        # vwma rising faster than ma — they should meet
        vwma = pd.Series(np.linspace(90.0, 150.0, n), index=idx)
        ma = pd.Series(np.linspace(100.0, 130.0, n), index=idx)
        out = ma_projection_meeting_point(vwma, ma, horizon=20)
        valid = out["bars_to_meet"].dropna()
        # Some bars should have finite meeting point
        assert (valid < np.inf).any(), "Converging lines must have finite meeting bars"

    def test_nan_input_handled_gracefully(self) -> None:
        idx = _make_idx(150)
        vwma = pd.Series(np.nan, index=idx, dtype=float)
        ma = pd.Series(np.nan, index=idx, dtype=float)
        out = ma_projection_meeting_point(vwma, ma, horizon=20)
        assert isinstance(out, pd.DataFrame)
        assert len(out) == 150

    def test_short_series_below_horizon(self) -> None:
        """Series shorter than horizon should return a valid (possibly NaN) DataFrame."""
        idx = _make_idx(10)
        vwma = pd.Series(np.arange(10.0), index=idx)
        ma = pd.Series(np.arange(10.0) + 1.0, index=idx)
        out = ma_projection_meeting_point(vwma, ma, horizon=20)
        assert isinstance(out, pd.DataFrame)
        assert len(out) == 10

    def test_projected_price_is_numeric(self) -> None:
        idx = _make_idx(150)
        rng = np.random.default_rng(42)
        vwma = pd.Series(100.0 + rng.normal(scale=1.0, size=150).cumsum(), index=idx)
        ma = pd.Series(100.0 + rng.normal(scale=0.5, size=150).cumsum(), index=idx)
        out = ma_projection_meeting_point(vwma, ma, horizon=10)
        valid_price = out["projected_price"].dropna()
        assert pd.api.types.is_float_dtype(valid_price)
