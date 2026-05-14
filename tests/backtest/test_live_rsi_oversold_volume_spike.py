"""Unit tests for LiveRsiOversoldVolumeSpike (#227 S1)."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_rsi_oversold_volume_spike import (
    LiveRsiOversoldVolumeSpike,
)


def _ohlcv(n: int, *, last_volume_multiplier: float = 1.0, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(0, 0.5, n))
    closes = np.maximum(closes, 1.0)
    base_volume = 1_000.0
    volumes = np.full(n, base_volume) + rng.normal(0, 50, n)
    volumes[-1] = base_volume * last_volume_multiplier
    index = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.001,
            "low": closes * 0.999,
            "close": closes,
            "volume": volumes,
        },
        index=index,
    )


def _ctx(history: pd.DataFrame, rsi_value: float | None) -> dict:
    rsi_series = (
        pd.Series([rsi_value] * len(history), index=history.index, dtype=float)
        if rsi_value is not None
        else pd.Series(dtype=float)
    )
    return {
        "ts": history.index[-1],
        "market_snapshot": {
            "symbol": "TEST",
            "history": history,
            "price": float(history["close"].iloc[-1]),
        },
        "factors": {"rsi": rsi_series},
    }


def _run(strategy, ctx) -> Signal | None:
    return asyncio.run(strategy.on_bar(ctx))


class TestLiveRsiOversoldVolumeSpike:
    def test_marker_inheritance(self):
        strategy = LiveRsiOversoldVolumeSpike()
        assert isinstance(strategy, LiveScannerMixin)
        assert strategy.is_live_scanner is True
        assert strategy.stop_loss_pct == 0.03
        assert strategy.take_profit_pct == 0.06

    def test_buy_when_rsi_oversold_and_volume_spike(self):
        strategy = LiveRsiOversoldVolumeSpike()
        history = _ohlcv(50, last_volume_multiplier=3.0)
        signal = _run(strategy, _ctx(history, rsi_value=25.0))
        assert signal is not None
        assert signal.action == "buy"
        assert signal.size == pytest.approx(0.05)
        assert "rsi=25.0" in signal.reason
        assert "vol_ratio=" in signal.reason

    def test_hold_when_rsi_above_threshold(self):
        strategy = LiveRsiOversoldVolumeSpike()
        history = _ohlcv(50, last_volume_multiplier=3.0)
        signal = _run(strategy, _ctx(history, rsi_value=45.0))
        assert signal.action == "hold"
        assert "rsi_above_threshold" in signal.reason

    def test_hold_when_volume_below_multiplier(self):
        strategy = LiveRsiOversoldVolumeSpike()
        history = _ohlcv(50, last_volume_multiplier=1.5)
        signal = _run(strategy, _ctx(history, rsi_value=20.0))
        assert signal.action == "hold"
        assert "volume_ratio_low" in signal.reason

    def test_hold_when_history_too_short(self):
        strategy = LiveRsiOversoldVolumeSpike()
        history = _ohlcv(10, last_volume_multiplier=3.0)
        signal = _run(strategy, _ctx(history, rsi_value=20.0))
        assert signal.action == "hold"
        assert signal.reason == "warmup"

    def test_hold_when_rsi_missing(self):
        strategy = LiveRsiOversoldVolumeSpike()
        history = _ohlcv(50, last_volume_multiplier=3.0)
        ctx = _ctx(history, rsi_value=None)
        signal = _run(strategy, ctx)
        assert signal.action == "hold"
        assert signal.reason == "rsi_missing"

    def test_invalid_default_size_raises(self):
        with pytest.raises(ValueError):
            LiveRsiOversoldVolumeSpike(default_size=0.0)
        with pytest.raises(ValueError):
            LiveRsiOversoldVolumeSpike(default_size=1.5)

    def test_confidence_within_unit_interval(self):
        strategy = LiveRsiOversoldVolumeSpike()
        history = _ohlcv(50, last_volume_multiplier=3.0)
        signal = _run(strategy, _ctx(history, rsi_value=10.0))
        assert signal.action == "buy"
        assert 0.0 <= (signal.confidence or 0.0) <= 1.0
