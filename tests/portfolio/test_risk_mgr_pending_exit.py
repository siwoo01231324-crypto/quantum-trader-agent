"""In-flight exit guard 회귀 (2026-05-21).

LivePositionRiskManager 가 stop/TP 발사 후 broker fill 이 도착해 position_store
가 held=0 으로 갱신되기 전까지 같은 (sid, symbol) 에 대해 *다시* stop 평가하지
않도록 차단. 미가드 시: 1초 간격 mark-price tick 마다 evaluate 가 돌면서 store
가 아직 갱신 안 됐을 때 동일 stop 을 또 fire → broker 에 redundant SELL →
이미 청산된 포지션 위에 새 SHORT 진입 (실측 NEARUSDT -135 short, 19:52:34).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from live.pnl_aggregator import PnLAggregator
from live.strategy_position_store import StrategyPositionStore
from portfolio.live_position_risk import LivePositionRiskManager


def _mgr_with_long(entry: float = 1.75, qty: int = 135):
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    mgr = LivePositionRiskManager(position_store=store, pnl_aggregator=pnl)
    mgr.register_strategy_policy(
        "scan", stop_loss_pct=0.005, take_profit_pct=0.01,
    )
    store.record_fill(
        strategy_id="scan", symbol="NEARUSDT", side="buy",
        qty=Decimal(str(qty)),
    )
    pnl.record_fill(
        strategy_id="scan", symbol="NEARUSDT", side="buy",
        qty=Decimal(str(qty)), price=Decimal(str(entry)),
    )
    return mgr, store, pnl


def test_pending_exit_blocks_redundant_stop_within_fill_window():
    """Stop 발사 후 fill 도착 전까지 같은 (sid, sym) 추가 stop 차단."""
    mgr, store, pnl = _mgr_with_long(entry=1.75, qty=135)
    now = datetime.now(timezone.utc)

    # First evaluate: 가격이 stop 선 아래 → SELL intent 1건 발사.
    first = mgr.evaluate("NEARUSDT", Decimal("1.74"), now)
    assert len(first) == 1
    assert first[0].side == "sell"
    assert first[0].qty == 135.0
    # _pending_exit 에 마킹 — broker fill 도착 전 가드.
    assert ("scan", "NEARUSDT") in mgr._pending_exit

    # Next tick (fill 아직 도착 안 함, store 여전히 +135) — 가드가 막아야 함.
    # 가격 더 떨어져도 추가 SELL 발사 X.
    second = mgr.evaluate("NEARUSDT", Decimal("1.71"), now)
    assert second == [], "in-flight exit guard 가 중복 SELL 차단해야"
    third = mgr.evaluate("NEARUSDT", Decimal("1.68"), now)
    assert third == [], "여러 tick 가도 계속 차단"


def test_pending_exit_clears_when_fill_arrives_and_held_zero():
    """Broker fill 도착해 store 가 held=0 이면 _pending_exit 자동 cleanup."""
    mgr, store, pnl = _mgr_with_long(entry=1.75, qty=135)
    now = datetime.now(timezone.utc)

    # Stop fire → pending 마킹.
    mgr.evaluate("NEARUSDT", Decimal("1.74"), now)
    assert ("scan", "NEARUSDT") in mgr._pending_exit

    # Broker fill 도착 시뮬: store + pnl 이 held=0 으로 갱신됨.
    store.record_fill(
        strategy_id="scan", symbol="NEARUSDT", side="sell",
        qty=Decimal("135"),
    )
    pnl.record_fill(
        strategy_id="scan", symbol="NEARUSDT", side="sell",
        qty=Decimal("135"), price=Decimal("1.74"),
    )

    # 다음 evaluate 에서 held=0 분기 → pending cleanup.
    mgr.evaluate("NEARUSDT", Decimal("1.74"), now)
    assert ("scan", "NEARUSDT") not in mgr._pending_exit


def test_pending_exit_does_not_affect_other_symbols():
    """가드는 (sid, symbol) 단위 — 다른 symbol 은 영향 없음."""
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    mgr = LivePositionRiskManager(position_store=store, pnl_aggregator=pnl)
    mgr.register_strategy_policy(
        "scan", stop_loss_pct=0.005, take_profit_pct=0.01,
    )
    # 두 종목 long 진입.
    for sym, qty, px in [("NEARUSDT", 135, "1.75"), ("TRXUSDT", 650, "0.36")]:
        store.record_fill(strategy_id="scan", symbol=sym, side="buy",
                          qty=Decimal(str(qty)))
        pnl.record_fill(strategy_id="scan", symbol=sym, side="buy",
                        qty=Decimal(str(qty)), price=Decimal(px))

    now = datetime.now(timezone.utc)
    # NEAR stop fire.
    near_intents = mgr.evaluate("NEARUSDT", Decimal("1.74"), now)
    assert len(near_intents) == 1
    assert ("scan", "NEARUSDT") in mgr._pending_exit
    assert ("scan", "TRXUSDT") not in mgr._pending_exit

    # TRX 도 stop 조건 → 별도 발사 가능해야 함 (가드 영향 X).
    trx_intents = mgr.evaluate("TRXUSDT", Decimal("0.358"), now)
    assert len(trx_intents) == 1
    assert trx_intents[0].symbol == "TRXUSDT"

    # NEAR 는 여전히 가드 — 새 evaluate 에서도 차단.
    again = mgr.evaluate("NEARUSDT", Decimal("1.70"), now)
    assert again == []


def test_pending_exit_reset_allows_re_entry_after_fill():
    """청산 fill 도착 → 가드 해제 → 다음 진입 시 정상 stop 평가 가능."""
    mgr, store, pnl = _mgr_with_long(entry=1.75, qty=135)
    now = datetime.now(timezone.utc)

    # 1차 stop + fill 도착.
    mgr.evaluate("NEARUSDT", Decimal("1.74"), now)
    store.record_fill(strategy_id="scan", symbol="NEARUSDT", side="sell",
                      qty=Decimal("135"))
    pnl.record_fill(strategy_id="scan", symbol="NEARUSDT", side="sell",
                    qty=Decimal("135"), price=Decimal("1.74"))
    mgr.evaluate("NEARUSDT", Decimal("1.74"), now)  # cleanup
    assert ("scan", "NEARUSDT") not in mgr._pending_exit

    # 새 진입 (cooldown 무관 — 본 가드만 검증).
    store.record_fill(strategy_id="scan", symbol="NEARUSDT", side="buy",
                      qty=Decimal("130"))
    pnl.record_fill(strategy_id="scan", symbol="NEARUSDT", side="buy",
                    qty=Decimal("130"), price=Decimal("1.73"))

    # 2차 stop 정상 평가 가능.
    intents = mgr.evaluate("NEARUSDT", Decimal("1.72"), now)
    assert len(intents) == 1
    assert intents[0].qty == 130.0
    assert ("scan", "NEARUSDT") in mgr._pending_exit
