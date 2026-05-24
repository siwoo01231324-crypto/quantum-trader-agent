"""Unit tests for LiveAirborneBbReversal (40% retracement BB reversal)."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_airborne_bb_reversal import LiveAirborneBbReversal
from signals.airborne_bb_reversal import (
    RETRACE_RATIO,
    AirborneSetup,
    find_active_long_setup,
    evaluate_long_fire,
)


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


def _run(strategy: LiveAirborneBbReversal, ctx: dict) -> Signal | None:
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


def _trend_then_long_breakout_then_retrace() -> pd.DataFrame:
    """Mild uptrend baseline → non-degenerate BB, then dramatic drop and
    40% retracement fire at bar -1.

    Verified (bollinger 20,2σ) at fixture construction:
        bar -3 (i=47): low=90.0  vs  bb_lower=98.79  → pierces (breakout)
        bar -4 (i=46): low=101.38 vs bb_lower=101.02 → does NOT pierce (clean breakout)
        → base = close[-3] = 96, extreme = low[-3] = 90
        bar -2:  low=88 (new extreme), close=91  vs trig=88+0.4*(96-88)=91.2 → no termination (91<91.2)
        bar -1:  low=88, close=94  vs trig=91.2  → close>=trig → FIRE
    """
    n = 50
    closes = np.linspace(100.0, 102.0, n).copy()
    opens = closes.copy()
    highs = closes + 0.5
    lows = closes - 0.5
    closes[-3], opens[-3], highs[-3], lows[-3] = 96.0, 101.9, 101.9, 90.0
    closes[-2], opens[-2], highs[-2], lows[-2] = 91.0, 96.0, 96.5, 88.0
    closes[-1], opens[-1], highs[-1], lows[-1] = 94.0, 91.0, 94.5, 88.0
    return _frame(opens, highs, lows, closes)


def _trend_then_breakout_pending() -> pd.DataFrame:
    """Same setup but bar -1's close stays BELOW trigger → pending, no fire.

    Verified: breakout @ -2 (low=90 pierces, prev low=101.38 doesn't), base=96,
    ext=88 after bar -1's low → trig = 88+0.4*(96-88) = 91.2. close[-1]=90 < 91.2.
    """
    n = 50
    closes = np.linspace(100.0, 102.0, n).copy()
    opens = closes.copy()
    highs = closes + 0.5
    lows = closes - 0.5
    closes[-2], opens[-2], highs[-2], lows[-2] = 96.0, 101.9, 101.9, 90.0
    closes[-1], opens[-1], highs[-1], lows[-1] = 90.0, 96.0, 96.5, 88.0
    return _frame(opens, highs, lows, closes)


def _trend_then_breakout_terminated_then_quiet() -> pd.DataFrame:
    """Breakout at -4 already fired at -3. Bars -2, -1 quiet → no_active_setup.

    Verified construction (non-degenerate band):
        bar -4 (i=46): low=88 vs bb_lower≈99 → pierces (breakout)
        bar -5 (i=45): low=101.34 vs bb_lower≈101 → doesn't (clean breakout)
        base = close[-4] = 96, ext = 88, trig = 88+0.4*(96-88) = 91.2
        bar -3: close=94 >= 91.2 → confirmed termination at -3
        bars -2, -1: in 96-100 range, no new breakout
    """
    n = 50
    closes = np.linspace(100.0, 102.0, n).copy()
    opens = closes.copy()
    highs = closes + 0.5
    lows = closes - 0.5
    closes[-4], opens[-4], highs[-4], lows[-4] = 96.0, 101.9, 101.9, 88.0
    closes[-3], opens[-3], highs[-3], lows[-3] = 94.0, 96.0, 94.5, 90.0
    closes[-2], opens[-2], highs[-2], lows[-2] = 96.0, 94.0, 96.5, 94.0
    closes[-1], opens[-1], highs[-1], lows[-1] = 97.0, 96.0, 97.5, 95.5
    return _frame(opens, highs, lows, closes)


def _no_breakout_anywhere() -> pd.DataFrame:
    """Flat history with tiny noise — no low ever pierces lower band by any margin.

    Note: with perfectly flat closes std=0 → lower band == close, so any low<close
    technically "pierces". Use slight variation so the band is non-degenerate and
    no real breakout occurs.
    """
    n = 50
    rng = np.random.default_rng(0)
    closes = 100.0 + rng.normal(0, 0.1, n).cumsum() * 0.0
    closes = np.full(n, 100.0) + rng.normal(0, 0.05, n)
    opens = closes - rng.normal(0, 0.01, n)
    highs = np.maximum(opens, closes) + 0.02
    lows = np.minimum(opens, closes) - 0.02
    return _frame(opens, highs, lows, closes)


class TestLiveAirborneBbReversal:
    def test_marker_inheritance(self):
        s = LiveAirborneBbReversal()
        assert isinstance(s, LiveScannerMixin)
        assert s.is_live_scanner is True

    def test_stop_tp_defaults(self):
        s = LiveAirborneBbReversal()
        assert s.stop_loss_pct == 0.03
        assert s.take_profit_pct == 0.06
        assert s.trailing_stop_pct is None

    def test_ctor_overrides_stop_tp(self):
        s = LiveAirborneBbReversal(
            stop_loss_pct=0.01, take_profit_pct=0.02, trailing_stop_pct=0.015,
        )
        assert s.stop_loss_pct == 0.01
        assert s.take_profit_pct == 0.02
        assert s.trailing_stop_pct == 0.015
        # Class-level still unchanged (instance attr shadows).
        assert LiveAirborneBbReversal.stop_loss_pct == 0.03

    def test_invalid_default_size_raises(self):
        with pytest.raises(ValueError):
            LiveAirborneBbReversal(default_size=1.5)
        with pytest.raises(ValueError):
            LiveAirborneBbReversal(default_size=0.0)

    def test_hold_when_warmup(self):
        s = LiveAirborneBbReversal()
        history = _trend_then_long_breakout_then_retrace().iloc[:10]
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert signal.reason == "warmup"

    def test_buy_on_40pct_retrace(self):
        s = LiveAirborneBbReversal()
        history = _trend_then_long_breakout_then_retrace()
        signal = _run(s, _ctx(history))
        assert signal.action == "buy", (
            f"expected buy on 40% retrace, got {signal.action}/{signal.reason}"
        )
        assert "airborne_long_fire" in signal.reason

    def test_hold_when_close_below_trigger(self):
        s = LiveAirborneBbReversal()
        history = _trend_then_breakout_pending()
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert "airborne_long_pending" in signal.reason

    def test_no_signal_after_setup_terminated(self):
        s = LiveAirborneBbReversal()
        history = _trend_then_breakout_terminated_then_quiet()
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        # Either no_active_setup (setup terminated and we don't look further back)
        # or no_active_setup. Should never be a fire.
        assert "fire" not in signal.reason

    def test_hold_when_no_breakout(self):
        s = LiveAirborneBbReversal()
        history = _no_breakout_anywhere()
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert "fire" not in signal.reason


class TestAirborneSetup:
    def test_long_trigger_formula(self):
        """Verify the dataclass's trigger() matches the documented formula.

        base=100, extreme=90 → swing=10 → trigger = 90 + 0.4*10 = 94.
        """
        setup = AirborneSetup(breakout_index=0, base=100.0, extreme=90.0)
        assert setup.trigger("long") == pytest.approx(94.0)

    def test_short_trigger_formula(self):
        """base=100, extreme=110 → trigger = 110 - 0.4*10 = 106."""
        setup = AirborneSetup(breakout_index=0, base=100.0, extreme=110.0)
        assert setup.trigger("short") == pytest.approx(106.0)

    def test_trigger_folds_current_extreme(self):
        """Pass deeper extreme via param → trigger should move down."""
        setup = AirborneSetup(breakout_index=0, base=100.0, extreme=90.0)
        # New low 85 → extreme effectively 85 → trigger = 85 + 0.4*15 = 91.
        assert setup.trigger("long", current_extreme=85.0) == pytest.approx(91.0)
        # Shallower low 95 → ignored (min preserves 90) → trigger stays 94.
        assert setup.trigger("long", current_extreme=95.0) == pytest.approx(94.0)


class TestFindActiveLongSetup:
    def test_returns_none_on_no_breakout(self):
        closes = pd.Series([100.0] * 30)
        lows = closes - 0.5
        # bb_lower equals close so lows below → would technically pierce on every
        # bar. Use a clearly-non-piercing band instead.
        bb_lower = pd.Series([95.0] * 30)
        setup = find_active_long_setup(
            low=lows, close=closes, bb_lower=bb_lower, max_lookback=10,
        )
        assert setup is None

    def test_detects_breakout(self):
        n = 25
        closes = pd.Series([100.0] * n, dtype=float).copy()
        lows = (closes - 0.5).copy()
        bb_lower = pd.Series([95.0] * n, dtype=float)
        # Bar 20: breakout (low=90 pierces 95; bar 19 low=99.5 does not pierce).
        lows.iloc[20] = 90.0
        closes.iloc[20] = 92.0
        # Bars 21..23: keep the setup ACTIVE — low stays at extreme 90,
        # close stays below trigger 90+0.4*(92-90)=90.8 so no termination.
        for j in (21, 22, 23):
            lows.iloc[j] = 90.0
            closes.iloc[j] = 90.5
        # Bar 24 (current): not evaluated by find_active_long_setup (caller's job).
        setup = find_active_long_setup(
            low=lows, close=closes, bb_lower=bb_lower, max_lookback=10,
        )
        assert setup is not None, "expected active setup, got None"
        assert setup.breakout_index == 20
        assert setup.base == pytest.approx(92.0)
        assert setup.extreme == pytest.approx(90.0)


class TestEvaluateLongFire:
    def test_fires_on_retrace(self):
        n = 25
        closes = pd.Series([100.0] * n, dtype=float).copy()
        opens = closes.copy()
        highs = closes + 0.5
        lows = (closes - 0.5).copy()
        bb_lower = pd.Series([95.0] * n, dtype=float)
        # Breakout at 22: low=90 (pierces 95), close=92 → base=92, ext=90,
        # trig = 90 + 0.4*(92-90) = 90.8.
        lows.iloc[22] = 90.0
        closes.iloc[22] = 92.0
        # Bar 23: keep setup active — close 90.5 < trig 90.8 (no termination).
        lows.iloc[23] = 90.0
        closes.iloc[23] = 90.5
        # Bar 24 (current): close 94 >= trig 90.8 → FIRE.
        lows.iloc[24] = 90.0
        closes.iloc[24] = 94.0
        history = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes})
        fires, setup, trigger = evaluate_long_fire(
            history=history, bb_lower=bb_lower, max_lookback=10,
        )
        assert fires
        assert setup is not None
        assert trigger == pytest.approx(90.8)

    def test_no_fire_without_breakout(self):
        n = 25
        history = pd.DataFrame({
            "open": [100.0] * n, "high": [100.5] * n,
            "low": [99.5] * n, "close": [100.0] * n,
        })
        bb_lower = pd.Series([95.0] * n)
        fires, setup, trigger = evaluate_long_fire(
            history=history, bb_lower=bb_lower, max_lookback=10,
        )
        assert not fires
        assert setup is None


def test_retrace_ratio_matches_reverse_engineered_value():
    """If this value changes, the spec doc 38-airborne... must change too."""
    assert RETRACE_RATIO == 0.4
