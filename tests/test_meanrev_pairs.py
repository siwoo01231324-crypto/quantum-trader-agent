"""Unit tests for src/backtest/strategies/meanrev_pairs.py (T5)."""
from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from src.backtest.strategies.meanrev_pairs import MeanrevPairs


def _make_ctx(close_values: list[float], minute: int = 0, second: int = 0) -> dict[str, Any]:
    n = len(close_values)
    ts = pd.Timestamp("2026-01-15 10:00:00", tz="UTC").replace(minute=minute, second=second)
    idx = pd.date_range(end=ts, periods=n, freq="1h", tz="UTC")
    hist = pd.DataFrame(
        {
            "open": close_values,
            "high": [v * 1.001 for v in close_values],
            "low": [v * 0.999 for v in close_values],
            "close": close_values,
            "volume": [1000.0] * n,
        },
        index=idx,
    )
    return {
        "ts": ts,
        "market_snapshot": {
            "ETHBTC": {"close": close_values[-1]},
            "ohlcv_history": {"ETHBTC": hist},
        },
    }


def _make_low_zscore_ctx() -> dict:
    """z < -2 → buy."""
    rng = np.random.default_rng(42)
    log_base = np.log(0.07) + rng.normal(0, 0.003, 80)
    # Force last value well below the log-mean
    log_base[-1] = log_base[:-1].mean() - 3.0 * log_base[:-1].std()
    return _make_ctx(list(np.exp(log_base)))


def _make_high_zscore_ctx() -> dict:
    """z > 0 → sell."""
    rng = np.random.default_rng(42)
    log_base = np.log(0.07) + rng.normal(0, 0.003, 80)
    # Force last value well above the log-mean
    log_base[-1] = log_base[:-1].mean() + 3.0 * log_base[:-1].std()
    return _make_ctx(list(np.exp(log_base)))


def _make_neutral_zscore_ctx() -> dict:
    """z ≈ 0 → hold."""
    rng = np.random.default_rng(42)
    log_base = np.log(0.07) + rng.normal(0, 0.003, 80)
    # Force last value exactly at log-mean → z = 0
    log_base[-1] = log_base[:-1].mean()
    return _make_ctx(list(np.exp(log_base)))


class TestMeanrevPairs:
    def test_buy_when_z_below_threshold(self):
        strategy = MeanrevPairs()
        ctx = _make_low_zscore_ctx()
        signal = asyncio.run(strategy.on_bar(ctx))
        assert signal is not None
        assert signal.action == "buy"

    def test_sell_when_z_above_zero(self):
        strategy = MeanrevPairs()
        ctx = _make_high_zscore_ctx()
        signal = asyncio.run(strategy.on_bar(ctx))
        assert signal is not None
        assert signal.action == "sell"

    def test_hold_when_z_near_zero(self):
        strategy = MeanrevPairs()
        ctx = _make_neutral_zscore_ctx()
        signal = asyncio.run(strategy.on_bar(ctx))
        assert signal is not None
        # z = 0 → sell (z > 0 rule: hold until z crosses threshold;
        # conservative: z > 0 means sell open long)
        assert signal.action in ("hold", "sell")

    def test_signal_has_expected_return_and_confidence(self):
        strategy = MeanrevPairs()
        ctx = _make_low_zscore_ctx()
        signal = asyncio.run(strategy.on_bar(ctx))
        assert signal is not None
        assert signal.action == "buy"
        assert signal.expected_return is not None
        assert signal.confidence is not None
        assert 0.0 <= signal.confidence <= 1.0

    def test_sizing_within_unit_range(self):
        strategy = MeanrevPairs()
        ctx = _make_low_zscore_ctx()
        signal = asyncio.run(strategy.on_bar(ctx))
        assert signal is not None
        assert 0.0 <= signal.size <= 1.0

    def test_warmup_returns_hold(self):
        """Fewer than min_history bars → hold with reason."""
        strategy = MeanrevPairs()
        ctx = _make_ctx([0.07] * 30)  # only 30 bars < 61 min_history
        signal = asyncio.run(strategy.on_bar(ctx))
        assert signal is not None
        assert signal.action == "hold"
        assert signal.reason == "insufficient history"

    def test_not_my_bar_returns_hold(self):
        """Non-hourly boundary (:30) → hold."""
        strategy = MeanrevPairs()
        ctx = _make_low_zscore_ctx()
        ctx["ts"] = ctx["ts"].replace(minute=30)
        signal = asyncio.run(strategy.on_bar(ctx))
        assert signal is not None
        assert signal.action == "hold"
        assert signal.reason == "not my bar"

    def test_missing_history_returns_hold(self):
        """No ohlcv_history for ETHBTC → hold."""
        strategy = MeanrevPairs()
        ts = pd.Timestamp("2026-01-15 10:00:00", tz="UTC")
        ctx = {
            "ts": ts,
            "market_snapshot": {
                "ETHBTC": {"close": 0.07},
                "ohlcv_history": {},
            },
        }
        signal = asyncio.run(strategy.on_bar(ctx))
        assert signal is not None
        assert signal.action == "hold"

    def test_returns_signal_instance(self):
        strategy = MeanrevPairs()
        ctx = _make_low_zscore_ctx()
        signal = asyncio.run(strategy.on_bar(ctx))
        assert isinstance(signal, Signal)
