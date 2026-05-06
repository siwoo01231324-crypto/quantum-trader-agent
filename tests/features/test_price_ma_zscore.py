"""Tests for src/features/price_ma_zscore.py (issue #185 W1)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.price_ma_zscore import price_ma_zscore


def _make_idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")


class TestPriceMaZscore:
    def test_returns_series(self) -> None:
        n = 200
        idx = _make_idx(n)
        close = pd.Series(np.linspace(100.0, 200.0, n), index=idx)
        ma = close.rolling(20).mean()
        out = price_ma_zscore(close, ma, lookback=100)
        assert isinstance(out, pd.Series)
        assert len(out) == n

    def test_warmup_nan(self) -> None:
        """Values must be NaN until lookback bars of valid (close-ma) are available."""
        n = 200
        idx = _make_idx(n)
        close = pd.Series(np.linspace(100.0, 200.0, n), index=idx)
        # ma has NaN for first ma_window-1=19 bars; dist also NaN there.
        # rolling(lookback=50) needs 50 valid dist bars → first valid at index 19+50-1=68.
        ma = close.rolling(20).mean()
        out = price_ma_zscore(close, ma, lookback=50)
        assert out.iloc[:68].isna().all()
        assert not pd.isna(out.iloc[68])

    def test_zero_mean_constant_gap(self) -> None:
        """When (close - ma) is constant, z-score should be 0 or NaN (zero std)."""
        n = 200
        idx = _make_idx(n)
        ma = pd.Series(np.full(n, 100.0), index=idx)
        close = pd.Series(np.full(n, 105.0), index=idx)
        out = price_ma_zscore(close, ma, lookback=50)
        valid = out.dropna()
        assert (valid.abs() < 1e-9).all() or valid.isna().all()

    def test_positive_zscore_when_above_ma(self) -> None:
        """When close has been consistently above MA, recent z-score > 0."""
        n = 300
        idx = _make_idx(n)
        rng = np.random.default_rng(10)
        prices = 100.0 + rng.normal(scale=0.5, size=n).cumsum()
        close = pd.Series(prices, index=idx)
        ma = close.rolling(20).mean()
        # Add a fixed positive offset to close so it's above MA
        close_above = close + 5.0
        out = price_ma_zscore(close_above, ma, lookback=100)
        valid = out.dropna()
        assert valid.iloc[-1] > 0

    def test_negative_zscore_when_below_ma(self) -> None:
        """When close is suddenly far below MA, z-score should be negative."""
        n = 300
        idx = _make_idx(n)
        rng = np.random.default_rng(11)
        prices = np.concatenate([
            100.0 + rng.normal(scale=0.5, size=250),
            np.linspace(100.0, 70.0, 50),
        ])
        close = pd.Series(prices, index=idx)
        ma = close.rolling(20).mean()
        out = price_ma_zscore(close, ma, lookback=100)
        valid = out.dropna()
        assert valid.iloc[-1] < 0

    def test_nan_close_propagates(self) -> None:
        idx = _make_idx(200)
        close = pd.Series(np.nan, index=idx, dtype=float)
        ma = pd.Series(np.nan, index=idx, dtype=float)
        out = price_ma_zscore(close, ma, lookback=50)
        assert isinstance(out, pd.Series)
        assert len(out) == 200

    def test_lookback_parameter_respected(self) -> None:
        """Shorter lookback should produce earlier first non-NaN value."""
        n = 300
        idx = _make_idx(n)
        close = pd.Series(np.linspace(100.0, 200.0, n), index=idx)
        # ma(10): first valid dist at index 9; zscore(lookback=20) valid at 9+20-1=28
        # zscore(lookback=100) valid at 9+100-1=108
        ma = close.rolling(10).mean()
        out_short = price_ma_zscore(close, ma, lookback=20)
        out_long = price_ma_zscore(close, ma, lookback=100)
        first_valid_short = out_short.first_valid_index()
        first_valid_long = out_long.first_valid_index()
        assert first_valid_short < first_valid_long
