"""LivePositionRiskManager 시간기반 청산(timeout) — 2026-06-15.

진입 후 max_hold_sec 경과 + TP/SL 미도달이면 시장가 청산. 거래소 네이티브
TP/SL 이 있어도 동작(거래소는 가격 임계만, 시간청산 안 함) → 상승장 숏이
무한정 깔리는 것 차단. sim 의 4봉/1h hold 와 동일 개념.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.live.pnl_aggregator import PnLAggregator
from src.live.strategy_position_store import StrategyPositionStore
from src.portfolio.live_position_risk import LivePositionRiskManager

_T0 = datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc)


def _mgr(*, side="sell", entry=100.0, qty=1.0, max_hold_sec=3600.0, native=None):
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    pnl.record_fill(strategy_id="sid", symbol="X", side=side,
                    qty=Decimal(str(qty)), price=Decimal(str(entry)))
    store.record_fill(strategy_id="sid", symbol="X", side=side, qty=Decimal(str(qty)))
    mgr = LivePositionRiskManager(
        position_store=store, pnl_aggregator=pnl,
        max_hold_sec=max_hold_sec, native_tpsl_check=native,
    )
    mgr.register_strategy_policy("sid", stop_loss_pct=0.005, take_profit_pct=0.011)
    return mgr, store, pnl


def test_timeout_fires_after_max_hold():
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    # 첫 평가 — 밴드 안(SL 100.5/TP 98.9), entry_ts=T0 stamp, 발동 없음.
    assert mgr.evaluate("X", Decimal("100.0"), _T0) == []
    # 1h+ 경과, 여전히 밴드 안 → timeout 청산.
    intents = mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(seconds=3601))
    assert len(intents) == 1
    assert intents[0].side == "buy"          # 숏 커버
    assert intents[0].reduce_only is True
    assert "time_exit" in intents[0].reason


def test_timeout_not_before_max_hold():
    mgr, _s, _p = _mgr(max_hold_sec=3600.0)
    assert mgr.evaluate("X", Decimal("100.0"), _T0) == []
    assert mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(seconds=1800)) == []


def test_timeout_disabled_when_none():
    mgr, _s, _p = _mgr(max_hold_sec=None)
    assert mgr.evaluate("X", Decimal("100.0"), _T0) == []
    # 거의 무한 경과해도 비활성이면 timeout 안 남.
    assert mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(days=10)) == []


def test_timeout_fires_even_with_native_tpsl():
    """거래소 네이티브 TP/SL 활성이어도 timeout 은 발동 (거래소는 시간청산 안 함)."""
    mgr, _s, _p = _mgr(max_hold_sec=3600.0, native=lambda s: True)
    assert mgr.evaluate("X", Decimal("100.0"), _T0) == []
    intents = mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(seconds=3601))
    assert len(intents) == 1
    assert "time_exit" in intents[0].reason


def test_native_still_skips_price_exit_before_timeout():
    """네이티브 활성 + SL 가격 도달 + max_hold 전 → synthetic 가격청산 안 함."""
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0,
                       native=lambda s: True)
    # 숏인데 +1% (SL) 이지만 네이티브가 담당 → synthetic 미발동.
    assert mgr.evaluate("X", Decimal("101.0"), _T0) == []


def test_price_sl_still_fires_before_timeout_when_no_native():
    """timeout 도입이 기존 가격 SL 을 안 깨뜨림 — 네이티브 없으면 SL 즉시 발동."""
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0, native=None)
    intents = mgr.evaluate("X", Decimal("100.6"), _T0)  # +0.6% > SL 0.5%
    assert len(intents) == 1
    assert "stop_loss" in intents[0].reason


def test_entry_ts_resets_on_flat_reentry():
    """청산(flat) 후 재진입은 timeout 타이머가 리셋된다."""
    mgr, store, pnl = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    mgr.evaluate("X", Decimal("100.0"), _T0)               # stamp T0
    # 외부 청산 → flat
    store.force_sync_position(strategy_id="sid", symbol="X", qty=Decimal("0"))
    pnl.reset_cost_basis("sid", "X") if hasattr(pnl, "reset_cost_basis") else None
    mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(seconds=10))  # flat → entry_ts pop
    # 재진입
    pnl.record_fill(strategy_id="sid", symbol="X", side="sell",
                    qty=Decimal("1"), price=Decimal("100"))
    store.record_fill(strategy_id="sid", symbol="X", side="sell", qty=Decimal("1"))
    mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(seconds=20))  # fresh stamp @ +20
    # +20 에서 1800s 뒤(=fresh 기준 30분 < 1h) → timeout 아직 안 남.
    assert mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(seconds=1820)) == []
