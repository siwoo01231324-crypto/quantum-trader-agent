"""Unit tests for LiveBreakoutWithAtrStop (#227 S4)."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_breakout_with_atr_stop import LiveBreakoutWithAtrStop


def _ohlcv(closes: np.ndarray) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {
            "open": closes, "high": closes * 1.002, "low": closes * 0.998,
            "close": closes, "volume": np.full(n, 1_000.0),
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


class TestLiveBreakoutWithAtrStop:
    def test_marker_and_trailing_attr(self):
        s = LiveBreakoutWithAtrStop()
        assert isinstance(s, LiveScannerMixin)
        # Trailing stop is the primary exit for this strategy.
        assert s.trailing_stop_pct is not None
        assert s.trailing_stop_pct == 0.04

    def test_buy_when_new_20_bar_high(self):
        s = LiveBreakoutWithAtrStop()
        n = 40
        closes = np.linspace(100, 110, n - 1).tolist() + [115.0]  # final = new high
        history = _ohlcv(np.array(closes))
        signal = _run(s, _ctx(history))
        assert signal.action == "buy"
        assert "atr_breakout" in signal.reason

    def test_hold_when_no_breakout(self):
        s = LiveBreakoutWithAtrStop()
        n = 40
        closes = np.linspace(100, 110, n).tolist()
        closes[-1] = 105.0  # below recent max
        history = _ohlcv(np.array(closes))
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert "no_breakout" in signal.reason

    def test_hold_when_warmup(self):
        s = LiveBreakoutWithAtrStop()
        history = _ohlcv(np.linspace(100, 110, 5))
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert signal.reason == "warmup"
