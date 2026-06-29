"""LivePositionRiskManager 채널청산(channel exit) — 2026-06-25.

Donchian 돌파 추세전략(`live_donchian_breakout_btcgate`)용 동적 청산 — 청산 기준이
고정 % 가 아니라 매 봉 갱신되는 채널 레벨(Donchian10 하단). `evaluate`(가격 임계)·
`sweep_timeouts`(시간) 와 **독립 additive 경로** 임을 박제 (기존 race-path 무영향).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd

from src.live.pnl_aggregator import PnLAggregator
from src.live.strategy_position_store import StrategyPositionStore
from src.portfolio.live_position_risk import LivePositionRiskManager

_T0 = datetime(2026, 6, 25, 0, 0, 0, tzinfo=timezone.utc)


def _mgr(*, side="buy", entry=100.0, qty=1.0):
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    pnl.record_fill(strategy_id="brk", symbol="X", side=side,
                    qty=Decimal(str(qty)), price=Decimal(str(entry)))
    store.record_fill(strategy_id="brk", symbol="X", side=side, qty=Decimal(str(qty)))
    mgr = LivePositionRiskManager(position_store=store, pnl_aggregator=pnl)
    mgr.register_strategy_policy("brk", stop_loss_pct=0.08, take_profit_pct=0.50)
    return mgr, store, pnl


def _hist(close: float, low: float = None):
    low = close if low is None else low
    idx = pd.date_range("2026-06-20", periods=12, freq="4h")
    c = [close] * 12
    return pd.DataFrame({"close": c, "low": [low] * 12,
                         "high": c, "open": c, "volume": [1.0] * 12}, index=idx)


def test_channel_exit_fires_when_close_below_level():
    mgr, _s, _p = _mgr(side="buy", entry=100.0)
    mgr.register_channel_exit("brk", lambda h: 95.0)  # 청산 레벨 95
    intents = mgr.sweep_channel_exits(_T0, lambda sym: _hist(94.0))  # close 94 < 95
    assert len(intents) == 1
    assert intents[0].side == "sell"
    assert intents[0].reduce_only is True
    assert "channel_exit" in intents[0].reason


def test_no_exit_when_close_above_level():
    mgr, _s, _p = _mgr(side="buy", entry=100.0)
    mgr.register_channel_exit("brk", lambda h: 95.0)
    assert mgr.sweep_channel_exits(_T0, lambda sym: _hist(110.0)) == []


def test_no_channel_strategies_returns_empty():
    mgr, _s, _p = _mgr()
    # 미등록 → 즉시 빈 리스트 (다른 전략 영향 0).
    assert mgr.sweep_channel_exits(_T0, lambda sym: _hist(1.0)) == []


def test_long_only_skips_short_position():
    mgr, _s, _p = _mgr(side="sell", entry=100.0)  # 숏
    mgr.register_channel_exit("brk", lambda h: 95.0)
    # 숏은 채널청산 대상 아님 (long-only 돌파).
    assert mgr.sweep_channel_exits(_T0, lambda sym: _hist(94.0)) == []


def test_history_none_skips():
    mgr, _s, _p = _mgr(side="buy")
    mgr.register_channel_exit("brk", lambda h: 95.0)
    assert mgr.sweep_channel_exits(_T0, lambda sym: None) == []


def test_level_none_skips():
    mgr, _s, _p = _mgr(side="buy")
    mgr.register_channel_exit("brk", lambda h: None)  # warmup → 레벨 없음
    assert mgr.sweep_channel_exits(_T0, lambda sym: _hist(94.0)) == []


def test_inflight_guard_blocks_double_exit():
    mgr, _s, _p = _mgr(side="buy", entry=100.0)
    mgr.register_channel_exit("brk", lambda h: 95.0)
    first = mgr.sweep_channel_exits(_T0, lambda sym: _hist(94.0))
    assert len(first) == 1
    # 같은 sweep 직후 — fill 미도착(_pending_exit guard) → 추가 발사 없음.
    second = mgr.sweep_channel_exits(_T0 + timedelta(seconds=1), lambda sym: _hist(94.0))
    assert second == []


def test_history_lookup_exception_does_not_raise():
    mgr, _s, _p = _mgr(side="buy")
    mgr.register_channel_exit("brk", lambda h: 95.0)

    def _boom(sym):
        raise RuntimeError("feed down")

    assert mgr.sweep_channel_exits(_T0, _boom) == []  # 조회 실패가 청산을 막지만 raise 안 함


def test_integration_with_strategy_channel_level():
    """실제 전략 channel_exit_level 로 sweep — 통합 박제."""
    from backtest.strategies.live_donchian_breakout_btcgate import (
        LiveDonchianBreakoutBtcGate,
    )
    strat = LiveDonchianBreakoutBtcGate()
    mgr, _s, _p = _mgr(side="buy", entry=100.0)
    mgr.register_channel_exit("brk", strat.channel_exit_level)
    # 직전 10봉 low 의 min = 90 → 현재 종가 89 < 90 → 청산.
    idx = pd.date_range("2026-06-20", periods=12, freq="4h")
    lows = [90.0] * 11 + [85.0]   # 직전10봉(현재 제외) low min=90
    closes = [100.0] * 11 + [89.0]
    hist = pd.DataFrame({"close": closes, "low": lows, "high": closes,
                         "open": closes, "volume": [1.0] * 12}, index=idx)
    intents = mgr.sweep_channel_exits(_T0, lambda sym: hist)
    assert len(intents) == 1
    assert "channel_exit" in intents[0].reason


class TestRegressionNoImpactOnExistingPaths:
    """채널청산 등록이 기존 evaluate/sweep_timeouts 를 건드리지 않음을 박제."""

    def test_evaluate_unaffected_by_channel_registration(self):
        mgr, _s, _p = _mgr(side="buy", entry=100.0)
        mgr.register_channel_exit("brk", lambda h: 95.0)
        # 가격 밴드 안(SL 92/TP 150) → evaluate 는 평소대로 발동 없음.
        assert mgr.evaluate("X", Decimal("100.0"), _T0) == []
        # SL 도달(−8%) → evaluate 정상 발동 (채널 등록 무관).
        intents = mgr.evaluate("X", Decimal("92.0"), _T0 + timedelta(seconds=1))
        assert len(intents) == 1
        assert "stop_loss" in intents[0].reason

    def test_sweep_timeouts_unaffected(self):
        # max_hold 없는 mgr — sweep_timeouts 는 빈 리스트(기존 동작), 채널과 독립.
        mgr, _s, _p = _mgr(side="buy")
        mgr.register_channel_exit("brk", lambda h: 95.0)
        assert mgr.sweep_timeouts(_T0) == []
