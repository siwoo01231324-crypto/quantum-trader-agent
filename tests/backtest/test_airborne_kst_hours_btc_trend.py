"""Regression — KST gate v3 ({1,3,5,7,9,14,18,21,22,23}) + BTC trend filter.

2026-06-23 v3 갱신 (한 달 신호 sim 기반 롱+양수 시각).

가드:
  1. KST gate = {1,3,5,7,9,14,18,21,22,23} (v3, 한 달 신호 sim 기반)
  2. BTC EMA200 하회 AND 24h 급락(<-2%) 둘 다 → LONG entry 차단 (short 그대로)
     (2026-06-19 AND/-2% 강화 — 옛 OR/-1% 은 횡보장 롱 과차단)
  3. 상승추세 + 24h 딥 단독 → 차단 안 함 (AND 미충족)
  4. BTC uptrend → 진입 정상 통과
  5. btc_trend_filter_enabled=False 면 byte-identical (회귀 방지)
  6. universe_ohlcv 키 없으면 graceful — 차단 안 함
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies.live_airborne_bb_reversal_kst_hours import (
    LiveAirborneBbReversalKstHours,
    _BTC_EMA_PERIOD_HOURS,
    _btc_is_downtrend,
)


# ── KST gate v3 ─────────────────────────────────────────────────────────────

def test_kst_gate_v2_is_5_hours():
    """{1,3,5,7,9,14,18,21,22,23} — v3 10시각 (한 달 신호 sim 기반)."""
    assert LiveAirborneBbReversalKstHours.kst_entry_hours == frozenset(
        {1, 3, 5, 7, 9, 14, 18, 21, 22, 23}
    )


def test_kst_gate_v2_excludes_11():
    """KST 11시는 v3 게이트 밖 → 차단."""
    assert 11 not in LiveAirborneBbReversalKstHours.kst_entry_hours


def test_kst_gate_v2_includes_7_and_20():
    """KST 7시 v3 포함, 20시 v3 제외 확인 (8시는 v3 에서 제외됨)."""
    hours = LiveAirborneBbReversalKstHours.kst_entry_hours
    assert 7 in hours
    assert 20 not in hours
    assert 8 not in hours


# ── _btc_is_downtrend helper ────────────────────────────────────────────────

def _btc_hist(close_series: pd.Series) -> pd.DataFrame:
    """min-OHLCV — close 만 의미."""
    return pd.DataFrame({
        "open": close_series.values, "high": close_series.values,
        "low": close_series.values, "close": close_series.values,
        "volume": [1000.0] * len(close_series),
    }, index=close_series.index)


def test_btc_is_downtrend_below_ema_and_drawdown_returns_true():
    """EMA200 하회 AND 24h 급락(<-2%) 둘 다 → downtrend (2026-06-19 AND/-2%)."""
    n = 300
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    # 1) 옛날 250봉 = 100 근처 평탄. 2) 최근 50봉 = 80 까지 하락 (24h −10% 급락 포함)
    closes = np.concatenate([np.full(250, 100.0),
                             np.linspace(100, 80, 50)])
    h = _btc_hist(pd.Series(closes, index=idx))
    is_down, reason = _btc_is_downtrend(h)
    assert is_down
    assert "btc_downtrend" in reason


def test_btc_uptrend_with_24h_dip_not_blocked():
    """AND/-2% 강화: 상승추세(EMA 위)면 24h 딥 단독으로는 차단 안 함 (옛 OR 와 차이)."""
    n = 300
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    # 상승 trend (EMA 위) 인데 직전 24h 만 급락 → 옛 OR 는 차단했으나 AND 는 통과
    closes = np.concatenate([np.linspace(50, 100, 270), np.linspace(100, 90, 30)])
    h = _btc_hist(pd.Series(closes, index=idx))
    is_down, _ = _btc_is_downtrend(h)
    assert not is_down  # EMA 위라 below_ema=False → AND 미충족


def test_btc_is_uptrend_returns_false():
    """건강한 상승 추세 → not downtrend."""
    n = 300
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    closes = np.linspace(50, 100, n)  # 점진 상승
    h = _btc_hist(pd.Series(closes, index=idx))
    is_down, _ = _btc_is_downtrend(h)
    assert not is_down


def test_btc_is_downtrend_insufficient_history_returns_false():
    """200봉 미달 → graceful False (long block 안 함)."""
    n = 50
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    h = _btc_hist(pd.Series([100.0] * n, index=idx))
    is_down, reason = _btc_is_downtrend(h)
    assert not is_down
    assert "insufficient" in reason


# ── on_bar override — BTC trend filter ─────────────────────────────────────

def _make_alt_hist(action_close_pattern: list[float]) -> pd.DataFrame:
    """alt history — last bar 가 close_pattern 의 마지막 값."""
    n = max(60, len(action_close_pattern))
    idx = pd.date_range("2026-01-01", periods=n, freq="1h",
                        tz=timezone.utc)
    if len(action_close_pattern) < n:
        pad = [100.0] * (n - len(action_close_pattern))
        closes = pad + action_close_pattern
    else:
        closes = action_close_pattern[-n:]
    return pd.DataFrame({
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1000.0] * n,
    }, index=idx)


@pytest.mark.asyncio
async def test_on_bar_filter_disabled_byte_identical(monkeypatch):
    """btc_trend_filter_enabled=False → super().on_bar 결과 그대로 (회귀 가드)."""
    strat = LiveAirborneBbReversalKstHours(btc_trend_filter_enabled=False)
    # super().on_bar 가 buy 반환하도록 mock
    sentinel = Signal(action="buy", size=0.05, reason="parent_buy")

    async def _mock_parent(self, ctx):
        return sentinel
    monkeypatch.setattr(
        LiveAirborneBbReversalKstHours.__mro__[1], "on_bar", _mock_parent,
    )
    ctx = {"ts": None, "market_snapshot": {"symbol": "ETHUSDT"}}
    result = await strat.on_bar(ctx)
    assert result is sentinel  # filter 적용 안 함


@pytest.mark.asyncio
async def test_on_bar_no_universe_ohlcv_graceful_pass(monkeypatch):
    """universe_ohlcv 키 없으면 차단 안 함 (backtest 구버전 호환)."""
    strat = LiveAirborneBbReversalKstHours(btc_trend_filter_enabled=True)
    sentinel = Signal(action="buy", size=0.05, reason="parent_buy")

    async def _mock_parent(self, ctx):
        return sentinel
    monkeypatch.setattr(
        LiveAirborneBbReversalKstHours.__mro__[1], "on_bar", _mock_parent,
    )
    ctx = {"ts": None, "market_snapshot": {"symbol": "ETHUSDT"}}
    result = await strat.on_bar(ctx)
    assert result is sentinel


@pytest.mark.asyncio
async def test_on_bar_btc_uptrend_passes_buy_through(monkeypatch):
    """BTC 상승 추세이면 buy intent 그대로 통과."""
    strat = LiveAirborneBbReversalKstHours()
    sentinel = Signal(action="buy", size=0.05, reason="parent_buy")

    async def _mock_parent(self, ctx):
        return sentinel
    monkeypatch.setattr(
        LiveAirborneBbReversalKstHours.__mro__[1], "on_bar", _mock_parent,
    )

    # BTC uptrend
    n = 300
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz=timezone.utc)
    btc_closes = np.linspace(50, 100, n)
    btc_hist = _btc_hist(pd.Series(btc_closes, index=idx))
    ctx = {
        "ts": idx[-1],
        "market_snapshot": {
            "symbol": "ETHUSDT",
            "history": _make_alt_hist([100.0]),
            "universe_ohlcv": {"BTCUSDT": btc_hist},
        },
    }
    result = await strat.on_bar(ctx)
    assert result.action == "buy"


@pytest.mark.asyncio
async def test_on_bar_btc_downtrend_blocks_buy(monkeypatch):
    """BTC 하락 추세이면 buy intent 차단 (hold 변환) — 6/04 incident 차단."""
    strat = LiveAirborneBbReversalKstHours()
    parent_buy = Signal(action="buy", size=0.05, reason="airborne_long_fire")

    async def _mock_parent(self, ctx):
        return parent_buy
    monkeypatch.setattr(
        LiveAirborneBbReversalKstHours.__mro__[1], "on_bar", _mock_parent,
    )

    # BTC downtrend (EMA200 아래 + 24h drawdown)
    n = 300
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz=timezone.utc)
    btc_closes = np.concatenate([np.full(250, 100.0), np.linspace(100, 80, 50)])
    btc_hist = _btc_hist(pd.Series(btc_closes, index=idx))
    ctx = {
        "ts": idx[-1],
        "market_snapshot": {
            "symbol": "ETHUSDT",
            "history": _make_alt_hist([100.0]),
            "universe_ohlcv": {"BTCUSDT": btc_hist},
        },
    }
    result = await strat.on_bar(ctx)
    assert result.action == "hold", (
        f"BTC downtrend 인데 LONG entry 통과 — filter 깨짐. got {result.action}"
    )
    assert "btc_trend_filter_long_blocked" in result.reason


@pytest.mark.asyncio
async def test_on_bar_btc_downtrend_passes_sell_through(monkeypatch):
    """SHORT entry 는 BTC 하락이라도 통과 — short 은 정상 진입."""
    strat = LiveAirborneBbReversalKstHours()
    parent_sell = Signal(action="sell", size=0.05, reason="airborne_short_fire")

    async def _mock_parent(self, ctx):
        return parent_sell
    monkeypatch.setattr(
        LiveAirborneBbReversalKstHours.__mro__[1], "on_bar", _mock_parent,
    )

    # BTC downtrend
    n = 300
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz=timezone.utc)
    btc_closes = np.concatenate([np.full(250, 100.0), np.linspace(100, 80, 50)])
    btc_hist = _btc_hist(pd.Series(btc_closes, index=idx))
    ctx = {
        "ts": idx[-1],
        "market_snapshot": {
            "symbol": "ETHUSDT",
            "history": _make_alt_hist([100.0]),
            "universe_ohlcv": {"BTCUSDT": btc_hist},
        },
    }
    result = await strat.on_bar(ctx)
    assert result.action == "sell"  # short 통과
