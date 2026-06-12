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
    def __init__(self): self.placed = []; self.cancelled = []; self._active = {}  # symbol->[oid]
    async def place_protective_order(self, *, symbol, side, qty, stop_price, kind):
        oid = f"{kind}-{len(self.placed)}"
        self.placed.append(dict(symbol=symbol, side=side, qty=qty, stop_price=stop_price, kind=kind, oid=oid))
        self._active.setdefault(symbol, []).append(oid)
        return oid
    async def cancel_protective_order(self, *, symbol, broker_order_id):
        self.cancelled.append(broker_order_id)
        for lst in self._active.values():
            if broker_order_id in lst: lst.remove(broker_order_id)
    async def get_net_positions(self):
        # broker 실제 net — 기본 빈 dict(=종목 net 0 → 취소 허용). 테스트가
        # _nets 로 override 가능.
        return getattr(self, "_nets", {})
    async def list_open_protective_orders(self, *, symbol=None):
        # 거래소 active TP/SL — place 후 active, cancel/포지션청산 시 사라짐.
        # 테스트가 _active[symbol]=[] 로 "거래소 자동취소(청산)" 시뮬 가능.
        return [{"orderId": o} for o in self._active.get(symbol, [])]


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
async def test_two_strategies_same_symbol_registers_once():
    # 2026-06-12 — 두 에어본 전략이 같은 종목 진입해도 whole-position TPSL 1세트만.
    a = _Adapter()
    store = _Store({"sid-air": [("BTCUSDT", -3.0)], "sid-wl": [("BTCUSDT", -3.0)]})
    c = _coord(a, store)
    await c.on_fill(symbol="BTCUSDT", side="sell", strategy_id="sid-air", fill=_Fill(100.0))
    await c.on_fill(symbol="BTCUSDT", side="sell", strategy_id="sid-wl", fill=_Fill(100.0))
    assert len(a.placed) == 2  # SL+TP 1세트 (4개 아님 — 이중등록 제거)


@pytest.mark.asyncio
async def test_stale_registration_reregisters_on_reentry():
    # PAXG/XAUT 회귀 fix (2026-06-12) — 포지션 청산(거래소 pos-TPSL auto-cancel)
    # 됐는데 _registered 플래그가 stale 로 남은 뒤 재진입 → 거래소에 active TP/SL
    # 없으니 stale 로 보고 재등록(naked 방지). 청산 fill 이 cannot-resolve orphan
    # 이라 net=0 정리 경로를 안 타는 실제 케이스.
    a = _Adapter()
    store = _Store({"sid-air": [("BTCUSDT", -3.0)]})
    c = _coord(a, store)
    await c.on_fill(symbol="BTCUSDT", side="sell", strategy_id="sid-air", fill=_Fill(100.0))
    assert len(a.placed) == 2
    a._active["BTCUSDT"] = []   # 포지션 청산 → 거래소 TP/SL auto-cancel (단 _registered stale)
    await c.on_fill(symbol="BTCUSDT", side="sell", strategy_id="sid-air", fill=_Fill(101.0))
    assert len(a.placed) == 4   # 재등록됨 (naked 아님)


@pytest.mark.asyncio
async def test_exit_keeps_protection_while_other_strategy_holds():
    # 한 전략 net 0 이어도 broker net>0(다른 전략 보유)면 취소 안 함.
    a = _Adapter(); a._nets = {"BTCUSDT": Decimal("-3.0")}  # broker 아직 보유
    store = _Store({"sid-air": [("BTCUSDT", -3.0)]})
    c = _coord(a, store)
    await c.on_fill(symbol="BTCUSDT", side="sell", strategy_id="sid-air", fill=_Fill(100.0))
    store._p["sid-air"] = []  # 이 전략만 net 0
    await c.on_fill(symbol="BTCUSDT", side="buy", strategy_id="sid-air", fill=_Fill(99.0))
    assert len(a.cancelled) == 0  # broker net 살아있음 → 보호 유지


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
