"""Unit tests for LiveAirborneBbReversalV11 (close-based + margin + body)."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_airborne_bb_reversal_v11 import LiveAirborneBbReversalV11


def _ctx(history: pd.DataFrame, symbol: str = "BTCUSDT") -> dict:
    return {
        "ts": history.index[-1],
        "market_snapshot": {
            "symbol": symbol, "history": history,
            "price": float(history["close"].iloc[-1]),
        },
        "factors": {},
    }


def _run(s, ctx): return asyncio.run(s.on_bar(ctx))


def _frame(opens, highs, lows, closes, *, vol=1000.0) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": np.full(n, vol),
    }, index=idx)


def _trend_close_break_with_margin_body() -> pd.DataFrame:
    """Uptrend then bar -3 closes BELOW bb_lower * (1-margin) with body >= 0.5%.

    Construction:
        baseline closes 100→102 (uptrend, BB has width)
        bar -3: open=101.9, close=96.0, low=95.0, high=101.9 → close < lower_thr,
                body=(101.9-96.0)/101.9 = 5.79% > 0.5% ✓
        bar -2: low=88, close=91 (stays below trigger)
        bar -1: low=88, close=94 (>= trigger ~ 91.2 → FIRE)
    """
    n = 50
    closes = np.linspace(100.0, 102.0, n).copy()
    opens = closes.copy()
    highs = closes + 0.5
    lows = closes - 0.5
    closes[-3], opens[-3], highs[-3], lows[-3] = 96.0, 101.9, 101.9, 95.0
    closes[-2], opens[-2], highs[-2], lows[-2] = 91.0, 96.0, 96.5, 88.0
    closes[-1], opens[-1], highs[-1], lows[-1] = 94.0, 91.0, 94.5, 88.0
    return _frame(opens, highs, lows, closes)


def _wick_only_break_no_close() -> pd.DataFrame:
    """Bar that pierces BB lower with LOW but close is INSIDE band → v1.1 must NOT trigger.

    v1 (high/low based) would trigger this. v1.1 (close-based) must reject.
    """
    n = 50
    closes = np.linspace(100.0, 102.0, n).copy()
    opens = closes.copy()
    highs = closes + 0.5
    lows = closes - 0.5
    # Bar -3: wick goes to 90 but close stays inside band at 101.5
    closes[-3], opens[-3], highs[-3], lows[-3] = 101.5, 101.8, 102.0, 90.0
    # Bar -2 and -1: no further activity
    return _frame(opens, highs, lows, closes)


def _small_body_break() -> pd.DataFrame:
    """Close-based break but body too small (< 0.5%) → v1.1 must reject."""
    n = 50
    closes = np.linspace(100.0, 102.0, n).copy()
    opens = closes.copy()
    highs = closes + 0.5
    lows = closes - 0.5
    # Bar -3: close just below thr but body tiny (open 96.1, close 96.0 → body 0.1%)
    closes[-3], opens[-3], highs[-3], lows[-3] = 96.0, 96.1, 96.5, 95.5
    return _frame(opens, highs, lows, closes)


class TestLiveAirborneBbReversalV11:
    def test_marker_inheritance(self):
        s = LiveAirborneBbReversalV11()
        assert isinstance(s, LiveScannerMixin)
        assert s.is_live_scanner is True

    def test_defaults(self):
        s = LiveAirborneBbReversalV11()
        assert s.min_close_margin == 0.001
        assert s.min_body_pct == 0.005
        assert s.stop_loss_pct == 0.03
        assert s.take_profit_pct == 0.06

    def test_ctor_overrides(self):
        s = LiveAirborneBbReversalV11(
            min_close_margin=0.0005, min_body_pct=0.003,
            stop_loss_pct=0.02, take_profit_pct=0.04,
        )
        assert s.min_close_margin == 0.0005
        assert s.min_body_pct == 0.003

    def test_invalid_args(self):
        with pytest.raises(ValueError):
            LiveAirborneBbReversalV11(default_size=0.0)
        with pytest.raises(ValueError):
            LiveAirborneBbReversalV11(min_close_margin=-0.1)
        with pytest.raises(ValueError):
            LiveAirborneBbReversalV11(min_body_pct=-0.01)

    def test_warmup(self):
        s = LiveAirborneBbReversalV11()
        history = _trend_close_break_with_margin_body().iloc[:10]
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert signal.reason == "warmup"

    def test_fires_on_close_break_and_retrace(self):
        s = LiveAirborneBbReversalV11()
        signal = _run(s, _ctx(_trend_close_break_with_margin_body()))
        assert "airborne_v11_fire" in signal.reason or signal.action == "buy", (
            f"expected fire, got {signal.action}/{signal.reason}"
        )

    def test_rejects_wick_only_break(self):
        """v1.1 must NOT detect a breakout when only the wick pierces BB."""
        s = LiveAirborneBbReversalV11()
        signal = _run(s, _ctx(_wick_only_break_no_close()))
        assert signal.action == "hold"
        # Must NOT be a fire (no breakout detected)
        assert "fire" not in signal.reason

    def test_rejects_small_body(self):
        """v1.1 must NOT detect breakout when body < min_body_pct."""
        s = LiveAirborneBbReversalV11()
        signal = _run(s, _ctx(_small_body_break()))
        assert signal.action == "hold"
        assert "fire" not in signal.reason

    def test_no_breakout_in_flat(self):
        s = LiveAirborneBbReversalV11()
        n = 50
        closes = np.linspace(100.0, 100.5, n)
        opens = closes
        highs = closes + 0.1
        lows = closes - 0.1
        history = _frame(opens, highs, lows, closes)
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
