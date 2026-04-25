"""Unit tests for MomoVolFiltered strategy."""
from __future__ import annotations

import asyncio
import math

import numpy as np
import pandas as pd
import pytest

from src.backtest.strategies.momo_vol_filtered import MomoVolFiltered
from src.backtest.protocol import Signal


def _make_ohlcv(n: int = 60, *, base: float = 100.0, trend: float = 0.001, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="4h")
    closes = base * np.cumprod(1 + trend + rng.normal(0, 0.005, n))
    highs = closes * (1 + rng.uniform(0.001, 0.01, n))
    lows = closes * (1 - rng.uniform(0.001, 0.01, n))
    opens = closes * (1 + rng.normal(0, 0.002, n))
    volumes = rng.uniform(100, 1000, n)
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes}, index=idx)


def _make_ctx(hist: pd.DataFrame, ts: pd.Timestamp | None = None) -> dict:
    if ts is None:
        ts = pd.Timestamp("2024-01-10 08:00:00", tz="UTC")
    return {
        "ts": ts,
        "market_snapshot": {
            "ohlcv_history": {"BTCUSDT": hist},
        },
    }


def run(coro):
    return asyncio.run(coro)


class TestBarBoundaryGuard:
    def test_non_4h_boundary_returns_hold(self):
        hist = _make_ohlcv(60)
        strat = MomoVolFiltered()
        ts = pd.Timestamp("2024-01-10 08:30:00", tz="UTC")  # :30 — not boundary
        ctx = _make_ctx(hist, ts=ts)
        sig = run(strat.on_bar(ctx))
        assert sig.action == "hold"
        assert sig.reason == "not my bar"

    def test_4h_boundary_passes(self):
        hist = _make_ohlcv(60)
        strat = MomoVolFiltered()
        ts = pd.Timestamp("2024-01-10 08:00:00", tz="UTC")  # UTC hour 8 % 4 == 0
        ctx = _make_ctx(hist, ts=ts)
        sig = run(strat.on_bar(ctx))
        assert sig.action in ("buy", "sell", "hold")

    def test_midnight_utc_is_boundary(self):
        hist = _make_ohlcv(60)
        strat = MomoVolFiltered()
        ts = pd.Timestamp("2024-01-10 00:00:00", tz="UTC")
        ctx = _make_ctx(hist, ts=ts)
        sig = run(strat.on_bar(ctx))
        assert sig.action in ("buy", "sell", "hold")
        assert sig.reason != "not my bar"


class TestInsufficientHistory:
    def test_none_history_returns_hold(self):
        strat = MomoVolFiltered()
        ts = pd.Timestamp("2024-01-10 08:00:00", tz="UTC")
        ctx = {"ts": ts, "market_snapshot": {"ohlcv_history": {}}}
        sig = run(strat.on_bar(ctx))
        assert sig.action == "hold"
        assert "insufficient" in sig.reason

    def test_short_history_returns_hold(self):
        hist = _make_ohlcv(10)  # less than MIN_HISTORY=27
        strat = MomoVolFiltered()
        ts = pd.Timestamp("2024-01-10 08:00:00", tz="UTC")
        ctx = _make_ctx(hist, ts=ts)
        sig = run(strat.on_bar(ctx))
        assert sig.action == "hold"
        assert "insufficient" in sig.reason


class TestVolFilter:
    def test_high_vol_blocks_entry(self):
        """When realized vol >= vol_ceiling, entry should not fire (hold or sell)."""
        # Build a highly volatile series
        rng = np.random.default_rng(99)
        n = 60
        idx = pd.date_range("2024-01-01", periods=n, freq="4h")
        # Very large daily moves to push annualized vol >> 0.80
        closes = 100.0 * np.cumprod(1 + rng.normal(0, 0.15, n))
        highs = closes * 1.05
        lows = closes * 0.95
        opens = closes
        vols = np.ones(n) * 100
        hist = pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols}, index=idx)
        strat = MomoVolFiltered(vol_ceiling=0.80)
        ts = pd.Timestamp("2024-01-10 08:00:00", tz="UTC")
        ctx = _make_ctx(hist, ts=ts)
        sig = run(strat.on_bar(ctx))
        # Should not be a buy since vol is sky-high
        assert sig.action != "buy" or sig.size == 0.0

    def test_emergency_sell_on_extreme_vol(self):
        """vol > vol_ceiling * 1.5 triggers emergency sell.

        We use a very low vol_ceiling (0.001) so that any realistic vol exceeds
        the threshold, guaranteeing the emergency-sell branch fires.
        """
        hist = _make_ohlcv(60, seed=77)
        # vol_ceiling=0.001 => even tiny volatility triggers emergency (vol > 0.0015)
        strat = MomoVolFiltered(vol_ceiling=0.001)
        ts = pd.Timestamp("2024-01-10 08:00:00", tz="UTC")
        ctx = _make_ctx(hist, ts=ts)
        sig = run(strat.on_bar(ctx))
        assert sig.action == "sell"
        assert "emergency" in sig.reason.lower()


class TestMACDEntryExit:
    def _build_macd_positive_hist(self, n: int = 80) -> pd.DataFrame:
        """Build a strongly trending series so MACD histogram is positive."""
        idx = pd.date_range("2024-01-01", periods=n, freq="4h")
        # Strong uptrend: 0.5% per bar, very low noise -> MACD histogram positive
        closes = 100.0 * np.cumprod(np.ones(n) * 1.005)
        highs = closes * 1.002
        lows = closes * 0.998
        opens = closes
        vols = np.ones(n) * 100
        return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols}, index=idx)

    def _build_macd_negative_hist(self, n: int = 80) -> pd.DataFrame:
        """Build a series where MACD histogram is negative at the last bar.

        Pattern: strong uptrend for first 60 bars, then sharp drop for last 20.
        Fast EMA reacts faster to the drop so MACD line < signal line -> histogram < 0.
        """
        idx = pd.date_range("2024-01-01", periods=n, freq="4h")
        up = np.cumprod(np.ones(60) * 1.01) * 100.0
        down = up[-1] * np.cumprod(np.ones(n - 60) * 0.97)
        closes = np.concatenate([up, down])
        highs = closes * 1.002
        lows = closes * 0.998
        opens = closes
        vols = np.ones(n) * 100
        return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols}, index=idx)

    def test_macd_positive_low_vol_gives_buy(self):
        hist = self._build_macd_positive_hist(80)
        strat = MomoVolFiltered(vol_ceiling=10.0)  # very high ceiling so vol filter never blocks
        ts = pd.Timestamp("2024-01-10 08:00:00", tz="UTC")
        ctx = _make_ctx(hist, ts=ts)
        sig = run(strat.on_bar(ctx))
        assert sig.action == "buy"
        assert sig.size > 0.0
        assert sig.size <= 1.0

    def test_macd_negative_gives_sell(self):
        hist = self._build_macd_negative_hist(80)
        strat = MomoVolFiltered(vol_ceiling=10.0)
        ts = pd.Timestamp("2024-01-10 08:00:00", tz="UTC")
        ctx = _make_ctx(hist, ts=ts)
        sig = run(strat.on_bar(ctx))
        assert sig.action == "sell"
        assert "MACD" in sig.reason


class TestSignalFields:
    def test_buy_signal_has_required_fields(self):
        strat = MomoVolFiltered(vol_ceiling=10.0)
        n = 80
        idx = pd.date_range("2024-01-01", periods=n, freq="4h")
        closes = 100.0 * np.cumprod(np.ones(n) * 1.005)
        highs = closes * 1.002
        lows = closes * 0.998
        hist = pd.DataFrame({
            "open": closes, "high": highs, "low": lows, "close": closes, "volume": np.ones(n) * 100
        }, index=idx)
        ts = pd.Timestamp("2024-01-10 08:00:00", tz="UTC")
        ctx = _make_ctx(hist, ts=ts)
        sig = run(strat.on_bar(ctx))
        if sig.action == "buy":
            assert sig.confidence is not None
            assert 0.0 <= sig.confidence <= 1.0
            assert sig.expected_return is not None
            assert isinstance(sig.reason, str)
            assert sig.size > 0.0

    def test_hold_has_zero_size(self):
        strat = MomoVolFiltered()
        ts = pd.Timestamp("2024-01-10 08:01:00", tz="UTC")  # not boundary
        hist = _make_ohlcv(60)
        ctx = _make_ctx(hist, ts=ts)
        sig = run(strat.on_bar(ctx))
        assert sig.action == "hold"
        assert sig.size == 0.0

    def test_sell_has_full_size(self):
        n = 80
        idx = pd.date_range("2024-01-01", periods=n, freq="4h")
        closes = 100.0 * np.cumprod(np.ones(n) * 0.995)
        highs = closes * 1.002
        lows = closes * 0.998
        hist = pd.DataFrame({
            "open": closes, "high": highs, "low": lows, "close": closes, "volume": np.ones(n) * 100
        }, index=idx)
        strat = MomoVolFiltered(vol_ceiling=10.0)
        ts = pd.Timestamp("2024-01-10 08:00:00", tz="UTC")
        ctx = _make_ctx(hist, ts=ts)
        sig = run(strat.on_bar(ctx))
        if sig.action == "sell":
            assert sig.size == 1.0
