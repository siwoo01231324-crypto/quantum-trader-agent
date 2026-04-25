"""Integration tests: MomoBtcV2 + MetaLabeler hook (Task #7, Issue #85).

Tests:
  1. metalabeler=None (default) — bypass, behavior identical to base strategy
  2. metalabeler injected, p_take >= threshold — signal passes through with win_probability set
  3. metalabeler injected, p_take < threshold — signal rejected with action="hold", reason="metalabeler_reject"
  4. win_probability value from metalabeler is propagated correctly on pass-through
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Bar, Signal
from backtest.strategies.momo_btc_v2 import MomoBtcV2
from signals.rsi import compute_rsi, detect_divergence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    closes = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
    closes = np.maximum(closes, 1.0)
    opens = closes * (1 + np.random.randn(n) * 0.001)
    highs = np.maximum(closes, opens) * (1 + np.abs(np.random.randn(n) * 0.002))
    lows = np.minimum(closes, opens) * (1 - np.abs(np.random.randn(n) * 0.002))
    volumes = np.abs(np.random.randn(n) * 1000 + 5000)
    index = pd.date_range("2024-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=index,
    )


def _make_bar(close: float = 100.0) -> Bar:
    return Bar(
        ts=pd.Timestamp("2024-01-01"),
        open=close,
        high=close * 1.001,
        low=close * 0.999,
        close=close,
        volume=1000.0,
    )


def _find_bullish_bar(ohlcv: pd.DataFrame, strategy: MomoBtcV2) -> int | None:
    """Return index of first bar with bullish divergence, or None."""
    close = ohlcv["close"]
    rsi = compute_rsi(close, strategy.RSI_PERIOD)
    div = detect_divergence(close, rsi, strategy.LOOKBACK)
    min_bars = strategy.RSI_PERIOD + strategy.LOOKBACK * 2 + 1
    for i in range(min_bars, len(div)):
        if div.iloc[i] == "bullish":
            return i
    return None


class _StubMetaLabeler:
    """Minimal stub satisfying MetaLabeler.win_probability contract."""

    def __init__(self, fixed_prob: float) -> None:
        self._prob = fixed_prob

    def win_probability(self, X: pd.DataFrame) -> np.ndarray:
        return np.array([self._prob] * len(X))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_metalabeler_none_default_bypass():
    """metalabeler=None must produce same signal as base strategy (regression gate)."""
    ohlcv = _make_ohlcv()
    strategy_base = MomoBtcV2()
    strategy_with_none = MomoBtcV2(metalabeler=None)

    idx = _find_bullish_bar(ohlcv, strategy_base)
    if idx is None:
        pytest.skip("No bullish divergence in synthetic data")

    history = ohlcv.iloc[: idx + 1]
    bar = _make_bar(float(history["close"].iloc[-1]))
    close = ohlcv["close"]
    rsi = compute_rsi(close, strategy_base.RSI_PERIOD)
    context = {"factors": {"rsi": rsi.iloc[: idx + 1]}}

    sig_base = strategy_base.on_bar(bar, history, context)
    sig_none = strategy_with_none.on_bar(bar, history, context)

    assert sig_base.action == sig_none.action
    assert sig_base.size == sig_none.size
    assert sig_base.reason == sig_none.reason


def test_metalabeler_above_threshold_passes_signal():
    """When p_take >= threshold, signal passes through with win_probability set."""
    ohlcv = _make_ohlcv()
    stub = _StubMetaLabeler(fixed_prob=0.75)
    strategy = MomoBtcV2(metalabeler=stub, metalabeler_threshold=0.5)

    idx = _find_bullish_bar(ohlcv, strategy)
    if idx is None:
        pytest.skip("No bullish divergence in synthetic data")

    history = ohlcv.iloc[: idx + 1]
    bar = _make_bar(float(history["close"].iloc[-1]))
    close = ohlcv["close"]
    rsi = compute_rsi(close, strategy.RSI_PERIOD)
    context = {"factors": {"rsi": rsi.iloc[: idx + 1]}}

    sig = strategy.on_bar(bar, history, context)

    assert sig.action == "buy", f"Expected 'buy', got '{sig.action}'"
    assert sig.win_probability == pytest.approx(0.75)


def test_metalabeler_below_threshold_rejects_signal():
    """When p_take < threshold, signal is rejected with action='hold' and reason='metalabeler_reject'."""
    ohlcv = _make_ohlcv()
    stub = _StubMetaLabeler(fixed_prob=0.2)
    strategy = MomoBtcV2(metalabeler=stub, metalabeler_threshold=0.5)

    idx = _find_bullish_bar(ohlcv, strategy)
    if idx is None:
        pytest.skip("No bullish divergence in synthetic data")

    history = ohlcv.iloc[: idx + 1]
    bar = _make_bar(float(history["close"].iloc[-1]))
    close = ohlcv["close"]
    rsi = compute_rsi(close, strategy.RSI_PERIOD)
    context = {"factors": {"rsi": rsi.iloc[: idx + 1]}}

    sig = strategy.on_bar(bar, history, context)

    assert sig.action == "hold", f"Expected 'hold', got '{sig.action}'"
    assert sig.size == 0.0
    assert sig.reason == "metalabeler_reject"
    assert sig.win_probability == pytest.approx(0.2)


def test_metalabeler_win_probability_exact_threshold():
    """p_take exactly at threshold (boundary) — should pass (>= threshold)."""
    ohlcv = _make_ohlcv()
    threshold = 0.5
    stub = _StubMetaLabeler(fixed_prob=threshold)
    strategy = MomoBtcV2(metalabeler=stub, metalabeler_threshold=threshold)

    idx = _find_bullish_bar(ohlcv, strategy)
    if idx is None:
        pytest.skip("No bullish divergence in synthetic data")

    history = ohlcv.iloc[: idx + 1]
    bar = _make_bar(float(history["close"].iloc[-1]))
    close = ohlcv["close"]
    rsi = compute_rsi(close, strategy.RSI_PERIOD)
    context = {"factors": {"rsi": rsi.iloc[: idx + 1]}}

    sig = strategy.on_bar(bar, history, context)

    # Exactly at threshold => passes
    assert sig.action == "buy"
    assert sig.win_probability == pytest.approx(threshold)
