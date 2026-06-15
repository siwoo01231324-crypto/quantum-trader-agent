"""reconciler 가 store 를 flat(0) 정리할 때 PnL cost_basis 도 리셋되어야 한다.

근본버그 (XRP 2026-06-15 11:01, v0.6.66 "force_sync 시 cost-basis 리셋" 미포함
후속): 다전략 동시보유 종목의 청산 fill 이 귀속 불가로 drop → PnL cost_basis 가
옛 진입가로 stale 잔존 → reconciler 가 store qty 만 0 으로 정리(cost_basis 방치)
→ 다음 진입과 blend 되어 평균진입가가 실제보다 낮아짐 → synthetic 이 stale 평균
으로 stop 평가해 조기 손절 오발동(-0.09% 청산). fix: reconciler force_sync(→0)
콜백에서 cost_basis 도 리셋 → 다음 진입이 깨끗한 평균으로 시작.
"""
from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from live.pnl_aggregator import PnLAggregator
from live.position_reconciler import PositionReconciler
from live.strategy_position_store import StrategyPositionStore


# ── 단위: reset_cost_basis ────────────────────────────────────────────────────

def test_reset_cost_basis_pops_key():
    pnl = PnLAggregator()
    pnl.record_fill(strategy_id="sid", symbol="XRPUSDT", side="sell",
                    qty=Decimal("72"), price=Decimal("1.1675"), fee=Decimal("0"))
    assert ("sid", "XRPUSDT") in pnl._cost_basis
    pnl.reset_cost_basis("sid", "XRPUSDT")
    assert ("sid", "XRPUSDT") not in pnl._cost_basis
    # 멱등 — 없는 키 reset 도 안전.
    pnl.reset_cost_basis("sid", "XRPUSDT")


def test_reset_does_not_touch_realized():
    pnl = PnLAggregator()
    pnl.record_fill(strategy_id="sid", symbol="XRPUSDT", side="sell",
                    qty=Decimal("72"), price=Decimal("1.20"), fee=Decimal("0"))
    pnl.record_fill(strategy_id="sid", symbol="XRPUSDT", side="buy",
                    qty=Decimal("72"), price=Decimal("1.19"), fee=Decimal("0"))
    realized_before = pnl.realtime
    pnl.reset_cost_basis("sid", "XRPUSDT")
    assert pnl.realtime == realized_before  # 실현손익 불변


def test_reset_clears_stale_blend_on_reentry():
    """stale cost_basis 가 새 진입과 blend 되던 버그가 reset 으로 사라진다."""
    pnl = PnLAggregator()
    # 07시 XRP 숏 진입 (cost basis 1.1675). 청산 fill 은 drop 됐다고 가정(미기록).
    pnl.record_fill(strategy_id="sid", symbol="XRPUSDT", side="sell",
                    qty=Decimal("72"), price=Decimal("1.1675"), fee=Decimal("0"))
    # reconcile 로 flat 정리 시 cost_basis 리셋 (fix).
    pnl.reset_cost_basis("sid", "XRPUSDT")
    # 11시 새 XRP 숏 @ 1.1816 — blend 없이 깨끗한 평균.
    pnl.record_fill(strategy_id="sid", symbol="XRPUSDT", side="sell",
                    qty=Decimal("126"), price=Decimal("1.1816"), fee=Decimal("0"))
    _held, avg = pnl._cost_basis[("sid", "XRPUSDT")]
    assert avg == Decimal("1.1816")  # stale 1.1675 와 blend 안 됨


# ── 통합: reconciler phantom-clear → cost_basis 리셋 (live_run 와이어링 복제) ──

class _FlatBroker:
    async def get_net_positions(self):
        return {}  # XRPUSDT 거래소 flat


def test_reconcile_flat_triggers_cost_basis_reset():
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    store.record_fill(strategy_id="sid", symbol="XRPUSDT", side="sell", qty=Decimal("72"))
    pnl.record_fill(strategy_id="sid", symbol="XRPUSDT", side="sell",
                    qty=Decimal("72"), price=Decimal("1.1675"), fee=Decimal("0"))

    # live_run 의 _sync_orch_live_entered 와이어링 복제.
    def on_synced(sid, sym, qty):
        if float(qty) == 0:
            pnl.reset_cost_basis(sid, sym)

    rec = PositionReconciler(
        position_store=store, broker=_FlatBroker(), on_position_synced=on_synced,
    )
    asyncio.run(rec.reconcile_once())

    assert store.get_positions("sid") == []                 # store flat
    assert ("sid", "XRPUSDT") not in pnl._cost_basis        # cost_basis 리셋됨
