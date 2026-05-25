"""Unit tests for live-scanner trend / regime indicators (2026-05-26)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backtest.strategies import _indicators


def _ohlcv_from_closes(closes: np.ndarray, *, bar_range: float = 0.002) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq="1min")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * (1 + bar_range),
            "low": closes * (1 - bar_range),
            "close": closes,
            "volume": np.full(n, 1_000.0),
        },
        index=idx,
    )


class TestEma:
    def test_warmup_nan_then_value(self):
        s = pd.Series(np.arange(1, 11, dtype=float))
        out = _indicators.ema(s, period=5)
        # First 4 values are NaN (min_periods=5)
        assert out.iloc[:4].isna().all()
        assert not pd.isna(out.iloc[-1])
        # 1..10 monotone increasing → EMA monotone increasing on valid range.
        valid = out.dropna()
        assert (valid.diff().dropna() > 0).all()

    def test_constant_series_equals_constant(self):
        s = pd.Series(np.full(20, 100.0))
        out = _indicators.ema(s, period=5)
        assert out.iloc[-1] == pytest.approx(100.0)

    def test_period_validation(self):
        with pytest.raises(ValueError):
            _indicators.ema(pd.Series([1.0]), period=0)


class TestAdx:
    def test_strong_uptrend_high_adx(self):
        # Pure monotone uptrend → ADX should climb well above 20.
        closes = np.linspace(100, 150, 60)
        df = _ohlcv_from_closes(closes)
        val = _indicators.adx(df, period=14)
        assert val is not None
        assert val > 20.0

    def test_flat_range_low_adx(self):
        # Sideways noise ~constant → ADX should stay low.
        rng = np.random.default_rng(42)
        closes = 100 + rng.normal(0, 0.05, 80)
        df = _ohlcv_from_closes(closes, bar_range=0.001)
        val = _indicators.adx(df, period=14)
        assert val is not None
        assert val < 25.0  # not trending

    def test_warmup_returns_none(self):
        df = _ohlcv_from_closes(np.linspace(100, 101, 10))
        assert _indicators.adx(df, period=14) is None

    def test_period_validation(self):
        with pytest.raises(ValueError):
            _indicators.adx(_ohlcv_from_closes(np.full(50, 100.0)), period=1)


class TestChoppinessIndex:
    def test_trending_market_below_threshold(self):
        closes = np.linspace(100, 200, 30)
        df = _ohlcv_from_closes(closes, bar_range=0.001)
        ci = _indicators.choppiness_index(df, period=14)
        assert ci is not None
        assert ci < _indicators.CHOPPINESS_TREND_DEFAULT  # < 38.2

    def test_choppy_market_above_threshold(self):
        # Oscillation pinned to same range → high CI.
        closes = 100 + 5 * np.sin(np.linspace(0, 8 * np.pi, 60))
        df = _ohlcv_from_closes(closes, bar_range=0.05)
        ci = _indicators.choppiness_index(df, period=14)
        assert ci is not None
        assert ci > _indicators.CHOPPINESS_RANGE_DEFAULT  # > 61.8

    def test_warmup_returns_none(self):
        df = _ohlcv_from_closes(np.full(5, 100.0))
        assert _indicators.choppiness_index(df, period=14) is None


class TestHurstExponent:
    def test_trending_higher_than_meanreverting(self):
        """Relative ordering: persistent process must score higher than
        anti-persistent on the same R/S estimator. Absolute H values from
        short-window R/S have known bias, so we only assert the *ranking*.
        """
        rng = np.random.default_rng(7)
        n = 400
        # Persistent: AR(1) with phi > 0 on the log-returns (momentum).
        returns_pers = [0.0]
        for _ in range(n):
            returns_pers.append(0.7 * returns_pers[-1] + 0.01 * rng.standard_normal())
        closes_pers = 100 * np.exp(np.cumsum(returns_pers[1:]))
        # Anti-persistent: AR(1) with phi < 0 (mean-reverting noise).
        returns_anti = [0.0]
        for _ in range(n):
            returns_anti.append(-0.7 * returns_anti[-1] + 0.01 * rng.standard_normal())
        closes_anti = 100 * np.exp(np.cumsum(returns_anti[1:]))
        h_pers = _indicators.hurst_exponent(pd.Series(closes_pers), lookback=200)
        h_anti = _indicators.hurst_exponent(pd.Series(closes_anti), lookback=200)
        assert h_pers is not None and h_anti is not None
        assert h_pers > h_anti

    def test_random_walk_returns_finite(self):
        rng = np.random.default_rng(0)
        closes = 100 + np.cumsum(rng.standard_normal(200) * 0.5)
        h = _indicators.hurst_exponent(pd.Series(closes), lookback=150)
        assert h is not None
        assert 0.0 < h < 1.5  # sane bounds for short-window R/S estimator

    def test_warmup_returns_none(self):
        closes = pd.Series(np.linspace(100, 110, 50))
        assert _indicators.hurst_exponent(closes, lookback=100) is None

    def test_lookback_validation(self):
        with pytest.raises(ValueError):
            _indicators.hurst_exponent(pd.Series([1.0] * 50), lookback=10)
