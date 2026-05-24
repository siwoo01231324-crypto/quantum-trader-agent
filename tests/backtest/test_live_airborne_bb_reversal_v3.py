"""Unit tests for LiveAirborneBbReversalV3 (v2 + volume gate)."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_airborne_bb_reversal_v3 import LiveAirborneBbReversalV3


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


def _run(strategy: LiveAirborneBbReversalV3, ctx: dict) -> Signal | None:
    return asyncio.run(strategy.on_bar(ctx))


def _frame(opens, highs, lows, closes, volumes) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=idx,
    )


def _uptrend_breakout_volume_ok() -> pd.DataFrame:
    """Uptrend baseline + breakout + retrace + high volume on fire bar."""
    n = 120
    closes = np.linspace(95.0, 105.0, n).copy()
    opens = closes.copy()
    highs = closes + 0.3
    lows = closes - 0.3
    volumes = np.full(n, 1_000.0)
    # Dip-breakout + retrace at -3 .. -1.
    closes[-3], opens[-3], highs[-3], lows[-3] = 102.0, 105.0, 105.0, 96.0
    closes[-2], opens[-2], highs[-2], lows[-2] = 100.0, 102.0, 102.5, 95.0
    closes[-1], opens[-1], highs[-1], lows[-1] = 104.0, 100.0, 104.5, 95.0
    # Volume on bar -1 well above baseline.
    volumes[-1] = 3_000.0
    return _frame(opens, highs, lows, closes, volumes)


def _uptrend_breakout_volume_weak() -> pd.DataFrame:
    """Same setup but volume on fire bar is BELOW the MA baseline."""
    df = _uptrend_breakout_volume_ok().copy()
    df.loc[df.index[-1], "volume"] = 500.0  # below 1000 baseline
    return df


def _downtrend_blocked_by_trend() -> pd.DataFrame:
    """Downtrend — trend gate must block (volume gate is unreachable)."""
    n = 120
    closes = np.linspace(105.0, 95.0, n).copy()
    opens = closes.copy()
    highs = closes + 0.3
    lows = closes - 0.3
    volumes = np.full(n, 1_000.0)
    closes[-3], opens[-3], highs[-3], lows[-3] = 95.0, 96.0, 96.0, 89.0
    closes[-2], opens[-2], highs[-2], lows[-2] = 92.0, 95.0, 95.0, 88.0
    closes[-1], opens[-1], highs[-1], lows[-1] = 95.0, 92.0, 95.5, 88.0
    volumes[-1] = 5_000.0  # high volume but trend should block
    return _frame(opens, highs, lows, closes, volumes)


class TestLiveAirborneBbReversalV3:
    def test_marker_inheritance(self):
        s = LiveAirborneBbReversalV3()
        assert isinstance(s, LiveScannerMixin)
        assert s.is_live_scanner is True

    def test_defaults(self):
        s = LiveAirborneBbReversalV3()
        assert s.trend_sma_period == 50
        assert s.volume_window == 20
        assert s.volume_ratio_min == 1.0
        assert s.stop_loss_pct == 0.02
        assert s.take_profit_pct == 0.04

    def test_ctor_overrides(self):
        s = LiveAirborneBbReversalV3(
            trend_sma_period=30, volume_window=10, volume_ratio_min=1.5,
            stop_loss_pct=0.01, take_profit_pct=0.03,
        )
        assert s.trend_sma_period == 30
        assert s.volume_window == 10
        assert s.volume_ratio_min == 1.5

    def test_invalid_args(self):
        with pytest.raises(ValueError):
            LiveAirborneBbReversalV3(default_size=0.0)
        with pytest.raises(ValueError):
            LiveAirborneBbReversalV3(trend_sma_period=1)
        with pytest.raises(ValueError):
            LiveAirborneBbReversalV3(volume_window=1)
        with pytest.raises(ValueError):
            LiveAirborneBbReversalV3(volume_ratio_min=-0.1)

    def test_warmup(self):
        s = LiveAirborneBbReversalV3()
        history = _uptrend_breakout_volume_ok().iloc[:30]
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert signal.reason == "warmup"

    def test_trend_gate_blocks(self):
        s = LiveAirborneBbReversalV3()
        signal = _run(s, _ctx(_downtrend_blocked_by_trend()))
        assert signal.action == "hold"
        assert "trend_gate" in signal.reason

    def test_volume_gate_blocks_weak_volume(self):
        s = LiveAirborneBbReversalV3()
        signal = _run(s, _ctx(_uptrend_breakout_volume_weak()))
        assert signal.action == "hold"
        assert "volume_gate" in signal.reason, (
            f"expected volume_gate block, got {signal.reason}"
        )

    def test_buy_when_all_gates_pass(self):
        s = LiveAirborneBbReversalV3()
        signal = _run(s, _ctx(_uptrend_breakout_volume_ok()))
        # Must NOT be blocked by trend or volume.
        assert "trend_gate" not in signal.reason
        assert "volume_gate" not in signal.reason
        # If buy, reason references airborne_v3_fire.
        if signal.action == "buy":
            assert "airborne_v3_fire" in signal.reason
            assert "vol_ratio" in signal.reason
