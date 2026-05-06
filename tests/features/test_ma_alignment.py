"""Tests for src/features/ma_alignment.py (issue #185 W1)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.ma_alignment import ma_aligned_pre_cross


def _make_idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")


def _trending_up(n: int, start: float = 100.0, step: float = 1.0) -> pd.Series:
    idx = _make_idx(n)
    return pd.Series(start + np.arange(n) * step, index=idx, dtype=float)


class TestMaAlignedPreCross:
    def test_returns_bool_series(self) -> None:
        close = _trending_up(200)
        out = ma_aligned_pre_cross(close, period_short=10, period_long=20, lookback=5)
        assert isinstance(out, pd.Series)
        assert out.dtype == bool

    def test_length_matches_input(self) -> None:
        close = _trending_up(200)
        out = ma_aligned_pre_cross(close, period_short=10, period_long=20, lookback=5)
        assert len(out) == len(close)

    def test_all_nan_warmup_is_false(self) -> None:
        """Before enough bars for both MAs the output must be False (not NaN)."""
        close = _trending_up(200)
        out = ma_aligned_pre_cross(close, period_short=50, period_long=100, lookback=10)
        # First 99 bars can't have full MA(100) — must all be False
        assert not out.iloc[:99].any()

    def test_cross_detected_within_lookback(self) -> None:
        """Craft a series that crosses short-MA above long-MA recently."""
        n = 300
        idx = _make_idx(n)
        # flat first 150 bars so MAs converge, then strong ramp so short MA
        # crosses above long MA
        prices = np.concatenate([
            np.full(150, 100.0),
            np.linspace(100.0, 160.0, 150),
        ])
        close = pd.Series(prices, index=idx, dtype=float)
        out = ma_aligned_pre_cross(close, period_short=20, period_long=50, lookback=20)
        # Somewhere in the ramp region there should be at least one True
        assert out.iloc[170:].any(), "Expected cross signal in ramp region"

    def test_already_crossed_long_ago_false(self) -> None:
        """If the cross happened more than lookback bars ago, signal must be False."""
        n = 400
        idx = _make_idx(n)
        # ramp for 200 bars, then flat — cross happened early
        prices = np.concatenate([
            np.linspace(100.0, 200.0, 200),
            np.full(200, 200.0),
        ])
        close = pd.Series(prices, index=idx, dtype=float)
        out = ma_aligned_pre_cross(close, period_short=20, period_long=50, lookback=5)
        # After a long flat period the old cross is outside lookback
        assert not out.iloc[-20:].any(), "Cross outside lookback should be False"

    def test_downtrend_always_false(self) -> None:
        """In a strict downtrend, short MA stays below long MA — never True."""
        n = 300
        idx = _make_idx(n)
        close = pd.Series(300.0 - np.arange(n) * 0.5, index=idx, dtype=float)
        out = ma_aligned_pre_cross(close, period_short=20, period_long=50, lookback=10)
        assert not out.any(), "Downtrend should never fire the pre-cross signal"

    def test_all_nan_input_returns_false(self) -> None:
        idx = _make_idx(200)
        close = pd.Series(np.nan, index=idx, dtype=float)
        out = ma_aligned_pre_cross(close, period_short=10, period_long=20, lookback=5)
        assert not out.any()

    def test_short_series_below_lookback(self) -> None:
        """Fewer bars than period_long should return all-False gracefully."""
        close = _trending_up(30)
        out = ma_aligned_pre_cross(close, period_short=50, period_long=100, lookback=10)
        assert not out.any()
