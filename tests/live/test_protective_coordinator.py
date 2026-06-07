"""ProtectiveOrderCoordinator 단위테스트 (2026-06-08).

진입(net≠0)→거래소 TP/SL 등록, 청산(net=0)→취소, orphan/정책없음→skip,
실패→fill 경로 안 깸.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.live.protective_coordinator import ProtectiveOrderCoordinator


class _Fill:
    def __init__(self, price): self.price = price


class _Adapter:
    def __init__(self): self.placed = []; self.cancelled = []
    async def place_protective_order(self, *, symbol, side, qty, stop_price, kind):
        oid = f"{kind}-{len(self.placed)}"
        self.placed.append(dict(symbol=symbol, side=side, qty=qty, stop_price=stop_price, kind=kind, oid=oid))
        return oid
    async def cancel_protective_order(self, *, symbol, broker_order_id):
        self.cancelled.append(broker_order_id)


class _Store:
    def __init__(self, positions): self._p = positions  # sid -> list[(sym, qty)]
    def get_positions(self, sid): return self._p.get(sid, [])


_POL = {"sid-air": (0.005, 0.01)}  # (sl_pct, tp_pct)
def _lookup(sid): return _POL.get(sid)


def _coord(adapter, store):
    return ProtectiveOrderCoordinator(adapter=adapter, position_store=store, policy_lookup=_lookup)


@pytest.mark.asyncio
async def test_short_entry_registers_sl_tp_at_correct_prices():
    a = _Adapter()
    store = _Store({"sid-air": [("BTCUSDT", -3.0)]})  # 숏 3
    c = _coord(a, store)
    await c.on_fill(symbol="BTCUSDT", side="sell", strategy_id="sid-air", fill=_Fill(100.0))
    assert len(a.placed) == 2
    sl = next(p for p in a.placed if p["kind"] == "STOP_MARKET")
    tp = next(p for p in a.placed if p["kind"] == "TAKE_PROFIT_MARKET")
    # 숏: SL = 100×1.005 = 100.5 (-5% ROI@10x), TP = 100×0.99 = 99.0 (+10% ROI)
    assert sl["stop_price"] == Decimal("100.500")
    assert tp["stop_price"] == Decimal("99.00")
    assert sl["side"] == "BUY" and tp["side"] == "BUY"  # 숏 청산 = BUY
    assert sl["qty"] == Decimal("3.0")


@pytest.mark.asyncio
async def test_second_fill_same_position_does_not_reregister():
    a = _Adapter()
    store = _Store({"sid-air": [("BTCUSDT", -3.0)]})
    c = _coord(a, store)
    await c.on_fill(symbol="BTCUSDT", side="sell", strategy_id="sid-air", fill=_Fill(100.0))
    await c.on_fill(symbol="BTCUSDT", side="sell", strategy_id="sid-air", fill=_Fill(100.0))
    assert len(a.placed) == 2  # 재등록 안 함


@pytest.mark.asyncio
async def test_exit_cancels_protection():
    a = _Adapter()
    store = _Store({"sid-air": [("BTCUSDT", -3.0)]})
    c = _coord(a, store)
    await c.on_fill(symbol="BTCUSDT", side="sell", strategy_id="sid-air", fill=_Fill(100.0))
    store._p["sid-air"] = []  # 청산 → net 0
    await c.on_fill(symbol="BTCUSDT", side="buy", strategy_id="sid-air", fill=_Fill(99.0))
    assert len(a.cancelled) == 2  # SL+TP 취소


@pytest.mark.asyncio
async def test_orphan_no_strategy_id_skips():
    a = _Adapter()
    c = _coord(a, _Store({}))
    await c.on_fill(symbol="BSBUSDT", side="sell", strategy_id=None, fill=_Fill(1.0))
    assert not a.placed


@pytest.mark.asyncio
async def test_no_policy_strategy_skips():
    a = _Adapter()
    store = _Store({"sid-cs": [("BTCUSDT", 5.0)]})
    c = _coord(a, store)
    await c.on_fill(symbol="BTCUSDT", side="buy", strategy_id="sid-cs", fill=_Fill(100.0))
    assert not a.placed  # cs-tsmom 류 = 정책 없음


@pytest.mark.asyncio
async def test_place_failure_does_not_raise():
    class _BadAdapter(_Adapter):
        async def place_protective_order(self, **kw):
            raise RuntimeError("bitget 40xx")
    store = _Store({"sid-air": [("BTCUSDT", -3.0)]})
    c = _coord(_BadAdapter(), store)
    # 예외가 밖으로 안 나와야 (fill 경로 보호)
    await c.on_fill(symbol="BTCUSDT", side="sell", strategy_id="sid-air", fill=_Fill(100.0))
