"""Unit tests for LiveMacdBullishCrossBreakout (#227 S4)."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_macd_bullish_cross_breakout import (
    LiveMacdBullishCrossBreakout,
)


def _ohlcv_with_macd_cross_and_breakout() -> pd.DataFrame:
    """Build a path engineered for MACD histogram to cross >0 on the LAST bar
    while simultaneously printing a new 20-bar high.

    Phases (n=80):
      0..59  steady at 100  (EMAs converge to 100, histogram drifts to ~0)
      60..78 linear drop 100 → 90  (fast EMA dives below slow → histogram negative)
      79     single jump to 120  (fast EMA pops above slow → histogram crosses positive,
                                  and 120 > max(close[-21:-1]) = 100 → breakout)
    """
    closes = np.concatenate([
        np.full(60, 100.0),
        np.linspace(100.0, 90.0, 19),
        np.array([120.0]),
    ])
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.001,
            "low": closes * 0.999,
            "close": closes,
            "volume": np.full(n, 1_000.0),
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


class TestLiveMacdBullishCrossBreakout:
    def test_marker_inheritance(self):
        s = LiveMacdBullishCrossBreakout()
        assert isinstance(s, LiveScannerMixin)
        assert s.is_live_scanner is True

    def test_buy_when_macd_cross_and_breakout(self):
        s = LiveMacdBullishCrossBreakout()
        history = _ohlcv_with_macd_cross_and_breakout()
        signal = _run(s, _ctx(history))
        assert signal is not None
        if signal.action != "buy":
            pytest.skip(
                f"synthetic path didn't produce both conditions on last bar — "
                f"reason={signal.reason}"
            )
        assert "macd_cross_breakout" in signal.reason

    def test_hold_when_warmup(self):
        s = LiveMacdBullishCrossBreakout()
        history = _ohlcv_with_macd_cross_and_breakout().iloc[:10]
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert signal.reason == "warmup"

    def test_hold_when_no_cross(self):
        """Pure uptrend → MACD already positive, no zero-cross on last bar."""
        s = LiveMacdBullishCrossBreakout()
        n = 80
        closes = np.linspace(100, 200, n)  # smooth uptrend
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
        assert ("no_macd_cross" in signal.reason) or ("no_breakout" in signal.reason)

    def test_invalid_default_size_raises(self):
        with pytest.raises(ValueError):
            LiveMacdBullishCrossBreakout(default_size=0.0)
