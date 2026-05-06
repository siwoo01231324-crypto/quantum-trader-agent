"""Tests for src/features/ma_magnet.py (issue #185 W1)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.features.ma_magnet import ma200_distance_zscore, return_to_ma_signal


def _make_idx(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")


class TestMa200DistanceZscore:
    def test_returns_series(self) -> None:
        n = 300
        idx = _make_idx(n)
        close = pd.Series(100.0 + np.random.default_rng(0).normal(size=n).cumsum(), index=idx)
        ma200 = close.rolling(200).mean()
        out = ma200_distance_zscore(close, ma200, window=200)
        assert isinstance(out, pd.Series)
        assert len(out) == n

    def test_zscore_near_zero_for_flat_distance(self) -> None:
        """When close - ma200 is constant the z-score should be ~0 (no variance)."""
        n = 400
        idx = _make_idx(n)
        # constant offset of 5
        ma200 = pd.Series(np.full(n, 100.0), index=idx)
        close = pd.Series(np.full(n, 105.0), index=idx)
        out = ma200_distance_zscore(close, ma200, window=200)
        valid = out.dropna()
        # std of constant series is 0 → z-score is 0 or NaN; either is acceptable
        assert (valid.abs() < 1e-9).all() or valid.isna().all()

    def test_warmup_is_nan(self) -> None:
        n = 300
        idx = _make_idx(n)
        close = pd.Series(np.linspace(100.0, 150.0, n), index=idx)
        ma200 = close.rolling(200).mean()
        out = ma200_distance_zscore(close, ma200, window=200)
        # First window-1 valid bars of (close-ma200) should be NaN
        assert out.iloc[:199].isna().all()

    def test_extreme_low_close_gives_negative_zscore(self) -> None:
        """When close is far below MA200 z-score should be significantly negative."""
        n = 400
        idx = _make_idx(n)
        rng = np.random.default_rng(1)
        # Build a mostly flat series, then crash at the end
        prices = np.concatenate([
            100.0 + rng.normal(scale=0.5, size=350),
            np.linspace(100.0, 60.0, 50),
        ])
        close = pd.Series(prices, index=idx)
        ma200 = close.rolling(200).mean()
        out = ma200_distance_zscore(close, ma200, window=200)
        valid = out.dropna()
        assert valid.iloc[-1] < -1.0, "Far-below-MA close should yield negative z-score"

    def test_nan_inputs_handled(self) -> None:
        idx = _make_idx(300)
        close = pd.Series(np.nan, index=idx, dtype=float)
        ma200 = pd.Series(np.nan, index=idx, dtype=float)
        out = ma200_distance_zscore(close, ma200)
        assert isinstance(out, pd.Series)
        assert len(out) == 300


class TestReturnToMaSignal:
    def test_returns_bool_series(self) -> None:
        n = 400
        idx = _make_idx(n)
        rng = np.random.default_rng(2)
        close = pd.Series(100.0 + rng.normal(size=n).cumsum(), index=idx)
        ma200 = close.rolling(200).mean()
        out = return_to_ma_signal(close, ma200, z_threshold=-1.5)
        assert isinstance(out, pd.Series)
        assert out.dtype == bool

    def test_signal_fires_when_deeply_oversold(self) -> None:
        """A sharp drop far below MA200 should trigger the return signal."""
        n = 400
        idx = _make_idx(n)
        rng = np.random.default_rng(3)
        prices = np.concatenate([
            100.0 + rng.normal(scale=0.3, size=350),
            np.linspace(100.0, 55.0, 50),
        ])
        close = pd.Series(prices, index=idx)
        ma200 = close.rolling(200).mean()
        out = return_to_ma_signal(close, ma200, z_threshold=-1.5)
        assert out.iloc[-10:].any(), "Signal should fire when far below MA200"

    def test_no_signal_above_ma(self) -> None:
        """When close is consistently above MA200 the signal should not fire."""
        n = 400
        idx = _make_idx(n)
        # strong uptrend — close always above MA200
        close = pd.Series(np.linspace(100.0, 300.0, n), index=idx)
        ma200 = close.rolling(200).mean()
        out = return_to_ma_signal(close, ma200, z_threshold=-1.5)
        # No signal expected after warmup
        assert not out.iloc[210:].any()

    def test_length_matches_input(self) -> None:
        n = 400
        idx = _make_idx(n)
        close = pd.Series(np.linspace(100.0, 200.0, n), index=idx)
        ma200 = close.rolling(200).mean()
        out = return_to_ma_signal(close, ma200)
        assert len(out) == n

    def test_threshold_sensitivity(self) -> None:
        """Looser threshold should fire more often than stricter one."""
        n = 400
        idx = _make_idx(n)
        rng = np.random.default_rng(4)
        prices = np.concatenate([
            100.0 + rng.normal(scale=0.5, size=300),
            np.linspace(100.0, 60.0, 100),
        ])
        close = pd.Series(prices, index=idx)
        ma200 = close.rolling(200).mean()
        loose = return_to_ma_signal(close, ma200, z_threshold=-0.5)
        strict = return_to_ma_signal(close, ma200, z_threshold=-2.5)
        assert loose.sum() >= strict.sum()
