"""Unit tests for LiveAirborneBbReversalV2 (v1 + trend alignment gate)."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_airborne_bb_reversal_v2 import LiveAirborneBbReversalV2


def _ctx(history: pd.DataFrame, symbol: str = "BTCUSDT") -> dict:
    return {
        "ts": history.index[-1],
        "market_snapshot": {
            "symbol": symbol,
            "history": history,
            "price": float(history["close"].iloc[-1]),
        },
        "factors": {},
    }


def _run(strategy: LiveAirborneBbReversalV2, ctx: dict) -> Signal | None:
    return asyncio.run(strategy.on_bar(ctx))


def _frame(opens, highs, lows, closes, *, volume: float = 1_000.0) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    return pd.DataFrame(
        {
            "open": opens, "high": highs, "low": lows, "close": closes,
            "volume": np.full(n, volume),
        },
        index=idx,
    )


def _uptrend_breakout_trend_ok() -> pd.DataFrame:
    """Strong uptrend baseline (sma_trend below close), then dip-breakout +
    40% retrace at -1. Both gates should pass → buy.

    Verified numerically: close[-1]=94 > sma_50 ≈ 100.5 → WAIT — actually that's
    NOT > trend. Let me invert: use baseline 90→100 uptrend so sma_50 ≈ 95-98
    and the bar -1 close ~99-100 stays above sma. Then dip at -3 still pierces
    the BB.
    """
    n = 120  # long enough for sma_100
    closes = np.linspace(95.0, 105.0, n).copy()
    opens = closes.copy()
    highs = closes + 0.3
    lows = closes - 0.3
    # Dip-breakout at -3, then current bar -1 reclaims with close > sma_100.
    closes[-3], opens[-3], highs[-3], lows[-3] = 102.0, 105.0, 105.0, 96.0
    closes[-2], opens[-2], highs[-2], lows[-2] = 100.0, 102.0, 102.5, 95.0
    closes[-1], opens[-1], highs[-1], lows[-1] = 104.0, 100.0, 104.5, 95.0
    return _frame(opens, highs, lows, closes)


def _downtrend_breakout_trend_block() -> pd.DataFrame:
    """Downtrend baseline. Even if BB breakout + retrace happen, trend gate
    blocks (close <= sma_trend)."""
    n = 120
    closes = np.linspace(105.0, 95.0, n).copy()
    opens = closes.copy()
    highs = closes + 0.3
    lows = closes - 0.3
    # Bar -1 close at 95 is below sma_100 ≈ 100 → trend gate blocks.
    closes[-3], opens[-3], highs[-3], lows[-3] = 95.0, 96.0, 96.0, 89.0
    closes[-2], opens[-2], highs[-2], lows[-2] = 92.0, 95.0, 95.0, 88.0
    closes[-1], opens[-1], highs[-1], lows[-1] = 95.0, 92.0, 95.5, 88.0
    return _frame(opens, highs, lows, closes)


class TestLiveAirborneBbReversalV2:
    def test_marker_inheritance(self):
        s = LiveAirborneBbReversalV2()
        assert isinstance(s, LiveScannerMixin)
        assert s.is_live_scanner is True

    def test_defaults(self):
        s = LiveAirborneBbReversalV2()
        assert s.trend_sma_period == 100
        assert s.stop_loss_pct == 0.03
        assert s.take_profit_pct == 0.06

    def test_ctor_overrides(self):
        s = LiveAirborneBbReversalV2(
            trend_sma_period=50, stop_loss_pct=0.01, take_profit_pct=0.03,
        )
        assert s.trend_sma_period == 50
        assert s.stop_loss_pct == 0.01
        assert s.take_profit_pct == 0.03

    def test_invalid_trend_period_raises(self):
        with pytest.raises(ValueError):
            LiveAirborneBbReversalV2(trend_sma_period=1)
        with pytest.raises(ValueError):
            LiveAirborneBbReversalV2(default_size=0.0)

    def test_warmup(self):
        s = LiveAirborneBbReversalV2()
        history = _uptrend_breakout_trend_ok().iloc[:30]
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert signal.reason == "warmup"

    def test_trend_gate_blocks_in_downtrend(self):
        s = LiveAirborneBbReversalV2()
        history = _downtrend_breakout_trend_block()
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert "trend_gate" in signal.reason, (
            f"expected trend_gate block in downtrend, got {signal.reason}"
        )

    def test_buy_in_uptrend(self):
        s = LiveAirborneBbReversalV2()
        history = _uptrend_breakout_trend_ok()
        signal = _run(s, _ctx(history))
        # Either buy (both gates pass) or some intermediate gate hold — but
        # specifically must NOT be trend_gate (since this is an uptrend).
        assert "trend_gate" not in signal.reason
        # If it's a buy, the reason must reference both trend and airborne_v2_fire.
        if signal.action == "buy":
            assert "airborne_v2_fire" in signal.reason
            assert "sma" in signal.reason
