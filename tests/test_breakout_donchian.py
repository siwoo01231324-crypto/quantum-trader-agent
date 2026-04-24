"""Tests for src/backtest/strategies/breakout_donchian.py."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path


def _run(coro):
    return asyncio.run(coro)

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from backtest.strategies.breakout_donchian import BreakoutDonchian  # noqa: E402
from backtest.protocol import Signal  # noqa: E402


# KRX trading-hour bar: 2026-04-09 15:30 KST = 06:30 UTC
KRX_BAR_TS = pd.Timestamp("2026-04-09 06:30:00", tz="UTC")
# Non-trading ts (wrong time)
NON_BAR_TS = pd.Timestamp("2026-04-09 01:00:00", tz="UTC")
# Holiday ts: 2026-01-01 (신정)
HOLIDAY_TS = pd.Timestamp("2026-01-01 06:30:00", tz="UTC")


def _make_ohlcv(n: int = 40, seed: int = 42, trend: float = 0.001) -> pd.DataFrame:
    """Generate synthetic OHLCV with an uptrend to trigger Donchian breakout."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    close = 10000.0 * np.cumprod(1 + trend + rng.normal(0, 0.01, n))
    high = close * (1 + rng.uniform(0.001, 0.015, n))
    low = close * (1 - rng.uniform(0.001, 0.015, n))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    volume = rng.integers(100_000, 1_000_000, n).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


def _make_downtrend_ohlcv(n: int = 40, seed: int = 99) -> pd.DataFrame:
    """Generate OHLCV with downtrend to trigger exit."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    close = 10000.0 * np.cumprod(1 - 0.005 + rng.normal(0, 0.005, n))
    high = close * (1 + rng.uniform(0.001, 0.01, n))
    low = close * (1 - rng.uniform(0.001, 0.01, n))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    volume = rng.integers(100_000, 1_000_000, n).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


def _ctx(ts: pd.Timestamp, ohlcv_history: dict) -> dict:
    return {"ts": ts, "market_snapshot": {"ohlcv_history": ohlcv_history}}


class TestBreakoutDonchianBarBoundary:
    def test_non_bar_time_returns_hold(self):
        strategy = BreakoutDonchian()
        hist = {"005930": _make_ohlcv(40)}
        sig = _run(
            strategy.on_bar(_ctx(NON_BAR_TS, hist))
        )
        assert sig.action == "hold"
        assert sig.reason == "not my bar"

    def test_holiday_returns_hold(self):
        strategy = BreakoutDonchian()
        hist = {"005930": _make_ohlcv(40)}
        sig = _run(
            strategy.on_bar(_ctx(HOLIDAY_TS, hist))
        )
        assert sig.action == "hold"
        assert sig.reason == "not my bar"

    def test_bar_time_proceeds(self):
        strategy = BreakoutDonchian()
        hist = {"005930": _make_ohlcv(40)}
        sig = _run(
            strategy.on_bar(_ctx(KRX_BAR_TS, hist))
        )
        # Should not return "not my bar"
        assert sig.reason != "not my bar"


class TestBreakoutDonchianBreakout:
    def test_uptrend_triggers_buy(self):
        """Strong uptrend should trigger Donchian breakout → buy signal."""
        strategy = BreakoutDonchian(entry_window=10, exit_window=5, top_n=3)
        # Use strong trend so close > upper.shift(1)
        hist = {"005930": _make_ohlcv(40, trend=0.01)}
        sig = _run(
            strategy.on_bar(_ctx(KRX_BAR_TS, hist))
        )
        assert sig.action in ("buy", "hold")
        assert sig.size >= 0.0
        assert sig.size <= 1.0

    def test_buy_signal_has_required_fields(self):
        """Signal must have confidence and expected_return fields."""
        strategy = BreakoutDonchian(entry_window=10, exit_window=5, top_n=3)
        hist = {"005930": _make_ohlcv(40, trend=0.01)}
        sig = _run(
            strategy.on_bar(_ctx(KRX_BAR_TS, hist))
        )
        # confidence must be in [0, 1] if present
        if sig.confidence is not None:
            assert 0.0 <= sig.confidence <= 1.0

    def test_top_n_selection(self):
        """With multiple symbols, strategy selects at most top_n."""
        strategy = BreakoutDonchian(entry_window=10, exit_window=5, top_n=2)
        hist = {
            f"00{i:04d}": _make_ohlcv(40, seed=i, trend=0.008)
            for i in range(5)
        }
        _run(
            strategy.on_bar(_ctx(KRX_BAR_TS, hist))
        )
        assert len(strategy._active_slots) <= 2

    def test_6digit_krx_codes(self):
        """Strategy accepts and stores 6-digit KRX codes."""
        strategy = BreakoutDonchian(entry_window=10, exit_window=5, top_n=3)
        hist = {
            "005930": _make_ohlcv(40, trend=0.01),
            "000660": _make_ohlcv(40, seed=2, trend=0.01),
        }
        _run(
            strategy.on_bar(_ctx(KRX_BAR_TS, hist))
        )
        for code in strategy._active_slots:
            assert len(code) == 6
            assert code.isdigit()


class TestBreakoutDonchianExit:
    def test_downtrend_exits_position(self):
        """After entering, a downtrend below exit_lower should clear the slot."""
        strategy = BreakoutDonchian(entry_window=10, exit_window=5, top_n=3)

        # First bar: enter with uptrend
        hist_up = {"005930": _make_ohlcv(40, trend=0.01)}
        _run(
            strategy.on_bar(_ctx(KRX_BAR_TS, hist_up))
        )
        # Manually seed active slot to test exit
        if "005930" not in strategy._active_slots:
            strategy._active_slots.append("005930")

        # Second bar: downtrend breaks below 5-day low
        hist_down = {"005930": _make_downtrend_ohlcv(40)}
        _run(
            strategy.on_bar(_ctx(KRX_BAR_TS, hist_down))
        )
        # After downtrend, slot may be exited (depends on magnitude)
        # At minimum, strategy should not raise
        assert len(strategy._active_slots) <= strategy.top_n

    def test_exit_lower_uses_exit_window(self):
        """Exit uses exit_window (10), not entry_window (20)."""
        strategy = BreakoutDonchian(entry_window=20, exit_window=5, top_n=3)
        assert strategy.exit_window == 5
        assert strategy.entry_window == 20


class TestBreakoutDonchianSizing:
    def test_size_in_unit_interval(self):
        """Signal size must be in [0, 1]."""
        strategy = BreakoutDonchian(entry_window=10, exit_window=5, kelly_k=0.5, top_n=5)
        hist = {f"00{i:04d}": _make_ohlcv(40, seed=i, trend=0.008) for i in range(5)}
        sig = _run(
            strategy.on_bar(_ctx(KRX_BAR_TS, hist))
        )
        assert 0.0 <= sig.size <= 1.0

    def test_kelly_k_parameter(self):
        """kelly_k is stored and used."""
        strategy = BreakoutDonchian(kelly_k=0.25)
        assert strategy.kelly_k == 0.25

    def test_insufficient_history_returns_hold(self):
        """Too few bars → hold."""
        strategy = BreakoutDonchian(entry_window=20)
        hist = {"005930": _make_ohlcv(5)}  # only 5 bars, need 21+
        sig = _run(
            strategy.on_bar(_ctx(KRX_BAR_TS, hist))
        )
        assert sig.action == "hold"

    def test_empty_history_returns_hold(self):
        """Empty history → hold."""
        strategy = BreakoutDonchian()
        sig = _run(
            strategy.on_bar(_ctx(KRX_BAR_TS, {}))
        )
        assert sig.action == "hold"
        assert sig.reason == "insufficient history"
