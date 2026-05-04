"""Tests for threshold-based regime classification.

TDD: synthetic price/funding data -> threshold classifier should assign
correct regimes based on rolling return and funding rate conditions.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.ml.regime.threshold import ThresholdRegime, ThresholdResult


def _make_synthetic_price(
    n_bars: int = 1000,
    seed: int = 42,
) -> pd.Series:
    """Generate synthetic close price with trend + mean-revert blocks."""
    rng = np.random.default_rng(seed)
    block = n_bars // 4
    prices = [100.0]
    for i in range(n_bars - 1):
        if i < block:
            drift = 0.002
        elif i < 2 * block:
            drift = -0.001
        elif i < 3 * block:
            drift = 0.003
        else:
            drift = -0.0005
        noise = rng.normal(0, 0.005)
        prices.append(prices[-1] * (1 + drift + noise))
    idx = pd.date_range("2020-01-01", periods=n_bars, freq="4h")
    return pd.Series(prices, index=idx, name="close")


def _make_synthetic_funding(
    n_bars: int = 1000,
    seed: int = 99,
) -> pd.Series:
    """Generate synthetic funding rate with negative/positive blocks."""
    rng = np.random.default_rng(seed)
    block = n_bars // 4
    funding = np.zeros(n_bars)
    funding[:block] = rng.normal(0.0001, 0.00005, block)
    funding[block : 2 * block] = rng.normal(-0.0003, 0.00005, block)
    funding[2 * block : 3 * block] = rng.normal(0.0002, 0.00005, 3 * block - 2 * block)
    funding[3 * block :] = rng.normal(-0.0002, 0.00005, n_bars - 3 * block)
    idx = pd.date_range("2020-01-01", periods=n_bars, freq="4h")
    return pd.Series(funding, index=idx, name="_funding_rate")


class TestThresholdRegime:
    def test_basic_classification(self):
        close = _make_synthetic_price()
        classifier = ThresholdRegime(return_lookback=180, return_threshold=0.0)
        result = classifier.classify(close)

        assert isinstance(result, ThresholdResult)
        assert len(result.states) == len(close)
        assert set(np.unique(result.states)).issubset({0, 2})
        assert result.labels[0] == "bullish"
        assert result.labels[2] == "neutral"

    def test_with_funding_rate(self):
        close = _make_synthetic_price()
        funding = _make_synthetic_funding()
        classifier = ThresholdRegime(
            return_lookback=180,
            return_threshold=0.0,
            funding_threshold=0.0,
        )
        result = classifier.classify(close, funding_rate=funding)

        assert set(np.unique(result.states)).issubset({0, 1, 2})
        assert result.labels[1] == "funding_negative"
        assert np.any(result.states == 1)

    def test_no_funding_no_state_1(self):
        close = _make_synthetic_price()
        classifier = ThresholdRegime()
        result = classifier.classify(close)

        assert 1 not in result.states

    def test_all_bullish_when_strong_uptrend(self):
        n = 500
        prices = 100.0 * np.exp(np.cumsum(np.full(n, 0.005)))
        idx = pd.date_range("2020-01-01", periods=n, freq="4h")
        close = pd.Series(prices, index=idx)

        classifier = ThresholdRegime(return_lookback=50, return_threshold=0.0)
        result = classifier.classify(close)

        valid = result.states[60:]
        bullish_pct = np.mean(valid == 0)
        assert bullish_pct > 0.90

    def test_custom_thresholds(self):
        close = _make_synthetic_price()
        strict = ThresholdRegime(return_lookback=180, return_threshold=0.5)
        result = strict.classify(close)

        bullish_pct = np.mean(result.states == 0)
        assert bullish_pct < 0.3

    def test_lookback_affects_result(self):
        close = _make_synthetic_price(n_bars=2000)
        short_lb = ThresholdRegime(return_lookback=30)
        long_lb = ThresholdRegime(return_lookback=500)

        r_short = short_lb.classify(close)
        r_long = long_lb.classify(close)

        assert not np.array_equal(r_short.states, r_long.states)

    def test_output_length_matches_input(self):
        for n in [100, 500, 1500]:
            close = _make_synthetic_price(n_bars=n)
            classifier = ThresholdRegime()
            result = classifier.classify(close)
            assert len(result.states) == n

    def test_shift_prevents_lookahead(self):
        close = _make_synthetic_price(n_bars=300)
        classifier = ThresholdRegime(return_lookback=50)
        result = classifier.classify(close)

        assert result.states[0] == 2
        for i in range(min(50, len(result.states))):
            assert result.states[i] == 2
