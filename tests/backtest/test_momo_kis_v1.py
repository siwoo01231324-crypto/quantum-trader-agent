"""Tests for MomoKisV1 AsyncStrategy (issue #96)."""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
import pytz

from backtest.strategies.momo_kis_v1 import MomoKisV1
from backtest.protocol import Signal
from universe.krx_calendar import KST


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int, start_price: float = 60000.0, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    closes = start_price + np.cumsum(np.random.randn(n) * 100.0)
    closes = np.maximum(closes, 1.0)
    opens = closes * (1 + np.random.randn(n) * 0.001)
    highs = np.maximum(closes, opens) * (1 + np.abs(np.random.randn(n) * 0.002))
    lows = np.minimum(closes, opens) * (1 - np.abs(np.random.randn(n) * 0.002))
    volumes = np.abs(np.random.randn(n) * 10000 + 50000)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes}
    )


def _kst_ts(year, month, day, hour, minute, second=0) -> pd.Timestamp:
    return pd.Timestamp(year, month, day, hour, minute, second, tzinfo=KST)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# _is_my_bar_boundary tests (5 cases)
# ---------------------------------------------------------------------------

def test_bar_boundary_weekday_kst_1000_true():
    """평일 KST 10:00 (15m 정시) → True."""
    s = MomoKisV1()
    ts = _kst_ts(2026, 4, 22, 10, 0)  # Wednesday
    assert s._is_my_bar_boundary(ts) is True


def test_bar_boundary_0907_false():
    """09:07 (정시 아님) → False."""
    s = MomoKisV1()
    ts = _kst_ts(2026, 4, 22, 9, 7)
    assert s._is_my_bar_boundary(ts) is False


def test_bar_boundary_saturday_false():
    """토요일 → False."""
    s = MomoKisV1()
    ts = _kst_ts(2026, 4, 25, 10, 0)  # Saturday
    assert s._is_my_bar_boundary(ts) is False


def test_bar_boundary_holiday_false():
    """휴일 (2026-01-01 신정) → False."""
    s = MomoKisV1()
    ts = _kst_ts(2026, 1, 1, 10, 0)  # New Year's Day
    assert s._is_my_bar_boundary(ts) is False


def test_bar_boundary_1600_false():
    """16:00 (장 마감 후) → False."""
    s = MomoKisV1()
    ts = _kst_ts(2026, 4, 22, 16, 0)
    assert s._is_my_bar_boundary(ts) is False


# ---------------------------------------------------------------------------
# warmup hold (1 case)
# ---------------------------------------------------------------------------

def test_warmup_returns_hold():
    """history < min_bars → Signal hold 'warmup'."""
    s = MomoKisV1()
    ts = _kst_ts(2026, 4, 22, 10, 0)
    history = _make_ohlcv(10)  # fewer than RSI_PERIOD + LOOKBACK*2 + 1 = 43
    ctx = {
        "ts": ts,
        "market_snapshot": {"history": history},
        "factors": {"rsi": pd.Series(dtype=float)},
    }
    sig = _run(s.on_bar(ctx))
    assert sig.action == "hold"
    assert sig.reason == "warmup"


# ---------------------------------------------------------------------------
# bullish → buy; bearish → sell (1 combined test)
# ---------------------------------------------------------------------------

def test_bullish_buy_bearish_sell():
    """bullish divergence → action='buy' size>0; bearish → action='sell' size=1.0."""
    # Trending up so mu > 0 and sigma > 0 → kelly > 0 → size > 0
    n = 100
    # 1% up each bar with small noise → positive mean return, non-zero sigma
    np.random.seed(7)
    pct = 0.01 + np.random.randn(n) * 0.005  # mean ~1%/bar
    closes = 60000.0 * np.cumprod(1.0 + pct)
    history = pd.DataFrame({
        "open": closes * 0.999,
        "high": closes * 1.002,
        "low": closes * 0.998,
        "close": closes,
        "volume": np.full(n, 50000.0),
    })

    s = MomoKisV1(sizing_mode="half-kelly", sizing_lookback=60)
    ts = _kst_ts(2026, 4, 22, 10, 0)

    # Patch detect_divergence to return bullish for last element
    bullish_series = pd.Series(["none"] * n)
    bullish_series.iloc[-1] = "bullish"

    with patch("backtest.strategies.momo_kis_v1.detect_divergence", return_value=bullish_series):
        ctx = {
            "ts": ts,
            "market_snapshot": {"history": history},
            "factors": {"rsi": pd.Series(np.full(n, 50.0))},
        }
        sig = _run(s.on_bar(ctx))
        assert sig.action == "buy"
        assert sig.size > 0.0

    # bearish case
    bearish_series = pd.Series(["none"] * n)
    bearish_series.iloc[-1] = "bearish"

    with patch("backtest.strategies.momo_kis_v1.detect_divergence", return_value=bearish_series):
        ctx = {
            "ts": ts,
            "market_snapshot": {"history": history},
            "factors": {"rsi": pd.Series(np.full(n, 50.0))},
        }
        sig = _run(s.on_bar(ctx))
        assert sig.action == "sell"
        assert sig.size == 1.0
