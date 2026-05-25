"""Unit tests for 2026-05-26 A+B+C entry filters in LiveScannerMixin.

A — trend filter (ADX + EMA slow)
B — anomaly guard (last close / ATR proxy == 0)
C — regime gate (Hurst + Choppiness vs regime_preference)

Each test exercises one filter in isolation by toggling that filter alone on
``LiveBreakoutWithAtrStop`` (the strategy chosen for ``regime_preference=trend``
out of the box). Strategies with other ``regime_preference`` are smoke-tested
to confirm the kwargs plumb through.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies.live_bb_lower_bounce import LiveBbLowerBounce
from backtest.strategies.live_breakout_with_atr_stop import LiveBreakoutWithAtrStop
from backtest.strategies.live_oversold_with_divergence import LiveOversoldWithDivergence
from backtest.strategies.live_rsi_oversold_volume_spike import LiveRsiOversoldVolumeSpike


def _ohlcv(closes: np.ndarray, *, bar_range: float = 0.002) -> pd.DataFrame:
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


def _ctx(history: pd.DataFrame, factors: dict | None = None) -> dict:
    return {
        "ts": history.index[-1],
        "market_snapshot": {
            "symbol": "BTCUSDT",
            "history": history,
            "price": float(history["close"].iloc[-1]),
        },
        "factors": factors or {},
    }


def _run(strategy, ctx) -> Signal | None:
    return asyncio.run(strategy.on_bar(ctx))


def _breakout_history(n: int = 60) -> pd.DataFrame:
    """Final bar is a clear 20-bar high — naive breakout would buy."""
    closes = np.linspace(100, 110, n - 1).tolist() + [120.0]
    return _ohlcv(np.array(closes))


class TestAnomalyGuard:
    def test_blocks_when_last_close_zero(self):
        s = LiveBreakoutWithAtrStop()  # anomaly_guard ON by default
        history = _breakout_history()
        history.iloc[-1, history.columns.get_loc("close")] = 0.0
        sig = _run(s, _ctx(history))
        assert sig.action == "hold"
        assert "anomaly" in sig.reason

    def test_blocks_when_atr_zero(self):
        # Flat OHLC with bar_range=0 → TR proxy zero → anomaly.
        s = LiveBreakoutWithAtrStop()
        n = 60
        closes = np.linspace(100, 110, n - 1).tolist() + [120.0]
        history = _ohlcv(np.array(closes), bar_range=0.0)
        # zero out the high-low spread for the last 15 bars
        history.iloc[-15:, history.columns.get_loc("high")] = history["close"].iloc[-15:]
        history.iloc[-15:, history.columns.get_loc("low")] = history["close"].iloc[-15:]
        sig = _run(s, _ctx(history))
        assert sig.action == "hold"
        assert "anomaly:atr_zero" in sig.reason

    def test_can_be_disabled(self):
        s = LiveBreakoutWithAtrStop(anomaly_guard_enabled=False)
        history = _breakout_history()
        # Even with last_close=0, anomaly guard OFF → other paths evaluated.
        history.iloc[-1, history.columns.get_loc("close")] = 0.0
        sig = _run(s, _ctx(history))
        # Without anomaly guard, breakout rule fires last_close=0 < prior_max
        # → no_breakout (hold for a different reason).
        assert sig.action == "hold"
        assert "anomaly" not in (sig.reason or "")


class TestTrendFilter:
    def test_passes_in_strong_uptrend(self):
        s = LiveBreakoutWithAtrStop(trend_filter_enabled=True, adx_threshold=15.0)
        history = _breakout_history(n=80)
        sig = _run(s, _ctx(history))
        assert sig.action == "buy"
        assert "atr_breakout" in sig.reason

    def test_blocks_when_adx_low_in_choppy_market(self):
        # Sideways noise → ADX stays below threshold, even when the final bar
        # ticks slightly above the prior 20-bar window. This is the BTC-on-5/25
        # scenario in synthetic form: micro-breakout in a sideways tape.
        rng = np.random.default_rng(0)
        n = 80
        closes = 100 + rng.normal(0, 0.1, n)
        closes[-1] = float(closes[-21:-1].max()) + 0.05  # marginal new high
        history = _ohlcv(closes, bar_range=0.001)
        # Precondition: naive breakout WOULD fire without the filter.
        assert closes[-1] > closes[-21:-1].max()
        s = LiveBreakoutWithAtrStop(trend_filter_enabled=True, adx_threshold=20.0)
        sig = _run(s, _ctx(history))
        assert sig.action == "hold"
        assert "trend_filter" in sig.reason

    def test_off_by_default(self):
        s = LiveBreakoutWithAtrStop()
        assert s.trend_filter_enabled is False


class TestRegimeFilter:
    def test_breakout_blocked_in_meanrev_regime(self):
        """Anti-persistent AR(1) returns (phi < 0) yield H < 0.5. trend-preference
        strategy must hold even if breakout would otherwise fire.
        """
        rng = np.random.default_rng(42)
        n = 250
        returns = [0.0]
        for _ in range(n):
            returns.append(-0.8 * returns[-1] + 0.005 * rng.standard_normal())
        closes = 100 * np.exp(np.cumsum(returns[1:]))
        # Force a 20-bar high on the final bar to provoke a breakout signal.
        closes[-1] = float(closes[-21:-1].max()) + 0.5
        history = _ohlcv(closes, bar_range=0.003)
        s = LiveBreakoutWithAtrStop(
            regime_filter_enabled=True,
            # Disable trend filter so we isolate the regime block.
            trend_filter_enabled=False,
            hurst_lookback=200,
            chop_period=14,
        )
        sig = _run(s, _ctx(history))
        assert sig.action == "hold"
        assert "regime_filter" in sig.reason

    def test_regime_off_by_default(self):
        s = LiveBreakoutWithAtrStop()
        assert s.regime_filter_enabled is False
        assert s.regime_preference == "trend"  # ClassVar override

    def test_meanrev_strategies_default_preference(self):
        assert LiveBbLowerBounce.regime_preference == "meanrev"
        assert LiveRsiOversoldVolumeSpike.regime_preference == "meanrev"
        assert LiveOversoldWithDivergence.regime_preference == "meanrev"


class TestKwargsPlumbing:
    """Smoke-test: every strategy accepts the filter kwargs without raising
    and stores them on the instance. Catches typos in any __init__ wiring.
    """

    @pytest.mark.parametrize(
        "cls",
        [
            LiveBreakoutWithAtrStop,
            LiveBbLowerBounce,
            LiveRsiOversoldVolumeSpike,
            LiveOversoldWithDivergence,
        ],
    )
    def test_accepts_filter_kwargs(self, cls):
        s = cls(
            anomaly_guard_enabled=True,
            trend_filter_enabled=True,
            regime_filter_enabled=True,
            regime_preference="meanrev",
            adx_threshold=25.0,
            ema_slow_period=100,
            hurst_lookback=150,
            chop_period=20,
        )
        assert s.anomaly_guard_enabled is True
        assert s.trend_filter_enabled is True
        assert s.regime_filter_enabled is True
        assert s.regime_preference == "meanrev"
        assert s.adx_threshold == 25.0
        assert s.ema_slow_period == 100
        assert s.hurst_lookback == 150
        assert s.chop_period == 20

    def test_invalid_regime_preference_rejected(self):
        with pytest.raises(ValueError):
            LiveBreakoutWithAtrStop(regime_preference="sideways")

    def test_negative_adx_threshold_rejected(self):
        with pytest.raises(ValueError):
            LiveBreakoutWithAtrStop(adx_threshold=-1.0)
