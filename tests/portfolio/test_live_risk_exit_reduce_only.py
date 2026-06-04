"""Regression — LivePositionRiskManager 의 청산 OrderIntent 는 항상 reduce_only=True.

2026-06-05 BEATUSDT 사고 안전망 2중. PR #362 (cross-run replay filter) 가
root cause 차단이고, 본 reduce_only=True 강제는 broker 측 자동 거부 안전망:

  - 어떤 이유로든 store qty > broker qty 인 상태에서 청산 발주가 들어가도
    Binance Futures 가 "reduceOnly with no position" 또는 "reduceOnly qty
    larger than position" 으로 reject → LONG/SHORT 뒤집기 사고 차단.

가드:
  1. LONG exit (sell): reduce_only=True
  2. SHORT exit (buy = cover): reduce_only=True
  3. TP/SL/trailing 모든 트리거에서 동일
  4. airborne 같은 bidir 전략의 short *진입* (orchestrator 생성) 는 본 함수
     영향 X — PR #342 shorts_allowed 가드 그대로 작동.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.live.pnl_aggregator import PnLAggregator
from src.live.strategy_position_store import StrategyPositionStore
from src.portfolio.live_position_risk import LivePositionRiskManager


def _setup_long(
    *, sid: str = "live-airborne-bb-reversal-kst-hours",
    symbol: str = "BTCUSDT",
    entry: float = 100.0, qty: float = 1.0,
    stop_loss_pct: float = 0.005, take_profit_pct: float = 0.010,
):
    """LONG 보유 시 LivePositionRiskManager."""
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    pnl.record_fill(strategy_id=sid, symbol=symbol, side="buy",
                    qty=Decimal(str(qty)), price=Decimal(str(entry)))
    store.record_fill(strategy_id=sid, symbol=symbol, side="buy",
                      qty=Decimal(str(qty)))
    mgr = LivePositionRiskManager(
        position_store=store, pnl_aggregator=pnl,
        wal_observer=lambda _e: None,
    )
    mgr.register_strategy_policy(
        sid, stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct,
        trailing_stop_pct=None,
    )
    return mgr, sid, symbol


def _setup_short(
    *, sid: str = "live-airborne-bb-reversal-kst-hours",
    symbol: str = "ETHUSDT",
    entry: float = 100.0, qty: float = 1.0,
):
    """SHORT 보유 (sell-first) 시 LivePositionRiskManager."""
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    pnl.record_fill(strategy_id=sid, symbol=symbol, side="sell",
                    qty=Decimal(str(qty)), price=Decimal(str(entry)))
    store.record_fill(strategy_id=sid, symbol=symbol, side="sell",
                      qty=Decimal(str(qty)))
    mgr = LivePositionRiskManager(
        position_store=store, pnl_aggregator=pnl,
        wal_observer=lambda _e: None,
    )
    mgr.register_strategy_policy(
        sid, stop_loss_pct=0.005, take_profit_pct=0.010,
        trailing_stop_pct=None,
    )
    return mgr, sid, symbol


# ── core: 모든 exit path 의 reduce_only ────────────────────────────────────

def test_long_sl_exit_sell_has_reduce_only_true():
    """LONG + SL 도달 → SELL intent reduce_only=True."""
    mgr, sid, sym = _setup_long(entry=100.0, qty=1.0, stop_loss_pct=0.005)
    intents = mgr.evaluate(sym, Decimal("99.4"), ts=datetime(2026, 6, 5, tzinfo=timezone.utc))
    assert intents
    intent = intents[0]
    assert intent.side == "sell"
    assert intent.reduce_only is True, (
        f"LONG exit (sell) reduce_only 가 False — over-shoot 안전망 깨짐. "
        f"got {intent.reduce_only!r}"
    )


def test_long_tp_exit_sell_has_reduce_only_true():
    """LONG + TP 도달 → SELL intent reduce_only=True."""
    mgr, sid, sym = _setup_long(entry=100.0, qty=1.0, take_profit_pct=0.010)
    intents = mgr.evaluate(sym, Decimal("101.1"), ts=datetime(2026, 6, 5, tzinfo=timezone.utc))
    assert intents
    assert intents[0].reduce_only is True


def test_short_sl_exit_buy_has_reduce_only_true():
    """SHORT + SL 도달 → BUY (cover) intent reduce_only=True.

    이게 깨지면 2026-06-05 BEATUSDT 사고 (SHORT 547 → BUY 634 → LONG 87 뒤집기)
    같은 over-shoot 가능. broker 가 reduce_only 로 자동 reject 해야 안전.
    """
    mgr, sid, sym = _setup_short(entry=100.0, qty=1.0)
    # SHORT 의 SL = entry +0.5% = 100.5
    intents = mgr.evaluate(sym, Decimal("100.6"), ts=datetime(2026, 6, 5, tzinfo=timezone.utc))
    assert intents
    intent = intents[0]
    assert intent.side == "buy", "SHORT exit 는 BUY (cover)"
    assert intent.reduce_only is True, (
        f"SHORT exit (buy) reduce_only 가 False — broker 가 over qty 또는 "
        f"보유 0 일 때 자동 reject 못 함 → naked long 으로 뒤집힐 위험. "
        f"got {intent.reduce_only!r}. BEATUSDT 사고 그대로 재발 가능."
    )


def test_short_tp_exit_buy_has_reduce_only_true():
    """SHORT + TP 도달 → BUY (cover) intent reduce_only=True."""
    mgr, sid, sym = _setup_short(entry=100.0, qty=1.0)
    # SHORT 의 TP = entry -1.0% = 99.0
    intents = mgr.evaluate(sym, Decimal("98.9"), ts=datetime(2026, 6, 5, tzinfo=timezone.utc))
    assert intents
    assert intents[0].side == "buy"
    assert intents[0].reduce_only is True
