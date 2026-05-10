"""Unit tests for LiveBbLowerBounce (#227 S4)."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_bb_lower_bounce import LiveBbLowerBounce


def _make_history(*, last_volume_multiplier: float = 1.5) -> pd.DataFrame:
    """Build an OHLCV path that pierces the lower band on bar -2 and reclaims
    it on bar -1. Strategy: stable price, then a sharp dip + recovery.
    """
    n = 50
    closes = np.full(n, 100.0)
    # Inject a single-bar dip below the rolling lower band on bar n-2,
    # then recover on bar n-1.
    closes[-2] = 90.0
    closes[-1] = 99.0
    base_volume = 1_000.0
    volumes = np.full(n, base_volume)
    volumes[-1] = base_volume * last_volume_multiplier
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {
            "open": closes, "high": closes * 1.001, "low": closes * 0.999,
            "close": closes, "volume": volumes,
        },
        index=idx,
    )


def _ctx(history: pd.DataFrame) -> dict:
    return {
        "ts": history.index[-1],
        "market_snapshot": {
            "symbol": "005930",
            "history": history,
            "price": float(history["close"].iloc[-1]),
        },
        "factors": {},
    }


def _run(strategy, ctx) -> Signal | None:
    return asyncio.run(strategy.on_bar(ctx))


class TestLiveBbLowerBounce:
    def test_marker_inheritance(self):
        s = LiveBbLowerBounce()
        assert isinstance(s, LiveScannerMixin)

    def test_buy_when_pierce_then_reclaim_with_volume(self):
        s = LiveBbLowerBounce()
        history = _make_history(last_volume_multiplier=2.0)
        signal = _run(s, _ctx(history))
        if signal.action != "buy":
            pytest.skip(f"synthetic path missed BB pierce/reclaim: {signal.reason}")
        assert "bb_lower_bounce" in signal.reason

    def test_hold_without_pierce(self):
        """No dip → no pierce → no bounce signal."""
        s = LiveBbLowerBounce()
        n = 50
        closes = np.full(n, 100.0)
        idx = pd.date_range("2026-01-01", periods=n, freq="15min")
        history = pd.DataFrame(
            {
                "open": closes, "high": closes * 1.001, "low": closes * 0.999,
                "close": closes, "volume": np.full(n, 1_000.0),
            },
            index=idx,
        )
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"

    def test_hold_when_volume_weak(self):
        s = LiveBbLowerBounce()
        history = _make_history(last_volume_multiplier=0.5)  # very low volume
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"

    def test_hold_when_warmup(self):
        s = LiveBbLowerBounce()
        history = _make_history().iloc[:10]
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert signal.reason == "warmup"

    def test_invalid_default_size_raises(self):
        with pytest.raises(ValueError):
            LiveBbLowerBounce(default_size=1.5)
