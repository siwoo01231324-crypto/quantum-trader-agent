"""Unit tests for LiveScannerEnsembleBn1d (Candidate C wrapper).

These tests verify the wrapper *behaviour*, not the underlying sub-strategy
edges. Sub-strategies are mocked with simple async callables that emit
controllable buy/hold signals so we can assert the wrapper's weight-sum +
half-kelly arithmetic deterministically. We also include one minimal
integration smoke test using the real sub-strategies on synthetic OHLCV to
verify the import + dispatch graph is wired correctly.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_scanner_ensemble_bn1d import LiveScannerEnsembleBn1d


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


def _run(strategy, ctx) -> Signal | None:
    return asyncio.run(strategy.on_bar(ctx))


def _flat_panel(n: int = 100, price: float = 100.0) -> pd.DataFrame:
    closes = np.full(n, price)
    idx = pd.date_range("2026-01-01", periods=n, freq="1D")
    return pd.DataFrame(
        {
            "open": closes, "high": closes * 1.001, "low": closes * 0.999,
            "close": closes, "volume": np.full(n, 1_000.0),
        },
        index=idx,
    )


@dataclass
class _StubSub:
    """Async stub with a fixed signal — used to drive deterministic tests."""
    name: str
    fire: bool  # if True, returns buy; else hold

    async def on_bar(self, ctx: Any) -> Signal:
        if self.fire:
            return Signal(action="buy", size=0.05, reason=f"stub:{self.name}")
        return Signal(action="hold", size=0.0, reason=f"stub:{self.name}:no")


def _make_wrapper_with_stubs(firing_names: set[str], *, half_kelly: float = 0.5
                              ) -> LiveScannerEnsembleBn1d:
    """Construct a wrapper, then swap _subs for stubs with controlled signals."""
    w = LiveScannerEnsembleBn1d(half_kelly=half_kelly)
    weights = w.weights
    w._subs = [
        ("rsi_oversold", _StubSub("rsi", "rsi_oversold" in firing_names),
         weights["rsi_oversold"]),
        ("breakout_atr", _StubSub("brk", "breakout_atr" in firing_names),
         weights["breakout_atr"]),
        ("bb_lower",     _StubSub("bb",  "bb_lower" in firing_names),
         weights["bb_lower"]),
        ("oversold_div", _StubSub("ov",  "oversold_div" in firing_names),
         weights["oversold_div"]),
    ]
    return w


class TestWrapperConstruction:
    def test_marker_inheritance(self):
        w = LiveScannerEnsembleBn1d()
        assert isinstance(w, LiveScannerMixin)
        assert w.is_live_scanner is True

    def test_stop_tp_defaults_match_subs(self):
        w = LiveScannerEnsembleBn1d()
        assert w.stop_loss_pct == 0.03
        assert w.take_profit_pct == 0.06
        assert w.trailing_stop_pct is None

    def test_default_weights_sum_to_one(self):
        w = LiveScannerEnsembleBn1d()
        assert abs(sum(w.weights.values()) - 1.0) < 1e-12

    def test_candidate_c_weights(self):
        w = LiveScannerEnsembleBn1d()
        assert w.weights["rsi_oversold"] == pytest.approx(0.30)
        assert w.weights["breakout_atr"] == pytest.approx(0.30)
        assert w.weights["bb_lower"] == pytest.approx(0.20)
        assert w.weights["oversold_div"] == pytest.approx(0.20)

    def test_half_kelly_default(self):
        w = LiveScannerEnsembleBn1d()
        assert w.half_kelly == 0.5

    def test_invalid_default_size_raises(self):
        with pytest.raises(ValueError):
            LiveScannerEnsembleBn1d(default_size=1.5)
        with pytest.raises(ValueError):
            LiveScannerEnsembleBn1d(default_size=0.0)

    def test_invalid_half_kelly_raises(self):
        with pytest.raises(ValueError):
            LiveScannerEnsembleBn1d(half_kelly=0.0)
        with pytest.raises(ValueError):
            LiveScannerEnsembleBn1d(half_kelly=1.5)

    def test_custom_weights_normalised(self):
        # Pass weights summing to 2.0 — should be normalised to sum 1.0.
        w = LiveScannerEnsembleBn1d(
            weights={"rsi_oversold": 0.6, "breakout_atr": 0.6,
                     "bb_lower": 0.4, "oversold_div": 0.4},
        )
        assert abs(sum(w.weights.values()) - 1.0) < 1e-12
        assert w.weights["rsi_oversold"] == pytest.approx(0.30)

    def test_unknown_weight_key_rejected(self):
        with pytest.raises(ValueError):
            LiveScannerEnsembleBn1d(weights={"foo": 1.0})

    def test_negative_weight_rejected(self):
        with pytest.raises(ValueError):
            LiveScannerEnsembleBn1d(weights={
                "rsi_oversold": -0.1, "breakout_atr": 0.5,
                "bb_lower": 0.3, "oversold_div": 0.3,
            })


class TestWrapperDispatch:
    def _ctx_warm(self) -> dict:
        return _ctx(_flat_panel(n=100))

    def test_warmup_hold(self):
        w = LiveScannerEnsembleBn1d()
        # 10 bars < MIN_HISTORY=60
        sig = _run(w, _ctx(_flat_panel(n=10)))
        assert sig.action == "hold"
        assert sig.reason == "warmup"

    def test_no_sub_buy_hold(self):
        w = _make_wrapper_with_stubs(firing_names=set())
        sig = _run(w, self._ctx_warm())
        assert sig.action == "hold"
        assert sig.reason == "no_sub_buy"

    def test_one_strong_sub_buy(self):
        # Only rsi_oversold (weight 0.30) fires; size = 0.05 × 0.30 × 0.5 = 0.0075
        w = _make_wrapper_with_stubs(firing_names={"rsi_oversold"})
        sig = _run(w, self._ctx_warm())
        assert sig.action == "buy"
        assert sig.size == pytest.approx(0.05 * 0.30 * 0.5, rel=1e-9)
        assert "rsi_oversold" in sig.reason
        assert "wsum=0.30" in sig.reason

    def test_one_weak_sub_buy(self):
        # bb_lower weight 0.20; size = 0.05 × 0.20 × 0.5 = 0.005
        w = _make_wrapper_with_stubs(firing_names={"bb_lower"})
        sig = _run(w, self._ctx_warm())
        assert sig.action == "buy"
        assert sig.size == pytest.approx(0.05 * 0.20 * 0.5, rel=1e-9)
        assert "bb_lower" in sig.reason

    def test_two_strong_subs_buy(self):
        # STRONG 2종 모두; size = 0.05 × 0.60 × 0.5 = 0.015
        w = _make_wrapper_with_stubs(
            firing_names={"rsi_oversold", "breakout_atr"})
        sig = _run(w, self._ctx_warm())
        assert sig.action == "buy"
        assert sig.size == pytest.approx(0.05 * 0.60 * 0.5, rel=1e-9)
        assert "wsum=0.60" in sig.reason

    def test_all_four_subs_buy_max_size(self):
        # All 4 sub-strategies fire; size = 0.05 × 1.00 × 0.5 = 0.025
        w = _make_wrapper_with_stubs(
            firing_names={"rsi_oversold", "breakout_atr",
                          "bb_lower", "oversold_div"})
        sig = _run(w, self._ctx_warm())
        assert sig.action == "buy"
        assert sig.size == pytest.approx(0.05 * 1.00 * 0.5, rel=1e-9)
        assert "wsum=1.00" in sig.reason

    def test_no_half_kelly_size_scales_up(self):
        # half_kelly=1.0 means no down-scaling; all 4 firing → size = default_size.
        w = _make_wrapper_with_stubs(
            firing_names={"rsi_oversold", "breakout_atr",
                          "bb_lower", "oversold_div"},
            half_kelly=1.0,
        )
        sig = _run(w, self._ctx_warm())
        assert sig.action == "buy"
        assert sig.size == pytest.approx(0.05, rel=1e-9)

    def test_quarter_kelly_size_halved(self):
        # half_kelly=0.25 → all 4 firing → size = 0.05 × 1.0 × 0.25 = 0.0125
        w = _make_wrapper_with_stubs(
            firing_names={"rsi_oversold", "breakout_atr",
                          "bb_lower", "oversold_div"},
            half_kelly=0.25,
        )
        sig = _run(w, self._ctx_warm())
        assert sig.action == "buy"
        assert sig.size == pytest.approx(0.05 * 0.25, rel=1e-9)


class TestIntegrationSmoke:
    """Verify real sub-strategies are wired correctly — minimal smoke."""

    def test_real_subs_warmup(self):
        w = LiveScannerEnsembleBn1d()
        # Short history → sub-strategies all return warmup → wrapper returns
        # warmup OR no_sub_buy (depending on which path hits first).
        sig = _run(w, _ctx(_flat_panel(n=10)))
        assert sig.action == "hold"

    def test_real_subs_dispatch_returns_valid_signal(self):
        """Wrapper wires the 4 real sub-strategies — they actually run and the
        wrapper emits a well-formed Signal. We don't assert the action because
        flat synthetic OHLCV (high≠low to keep BB defined) can spuriously
        trip an oversold sub on std=0 edge cases — that's a sub-strategy
        property, not a wrapper property."""
        w = LiveScannerEnsembleBn1d()
        sig = _run(w, _ctx(_flat_panel(n=100)))
        assert sig is not None
        assert sig.action in {"hold", "buy"}
        # If a buy fired, size obeys the wrapper formula (Σw × half_kelly × 0.05)
        if sig.action == "buy":
            assert 0 < sig.size <= 0.05 * 0.5  # max possible = all 4 × hk 0.5
            assert "ensemble(" in sig.reason
            assert "wsum=" in sig.reason
        else:
            assert sig.reason == "no_sub_buy"
