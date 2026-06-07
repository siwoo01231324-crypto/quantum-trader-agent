"""OrphanGuard 단위테스트 (2026-06-08) — store↔broker 드리프트 복구.

핵심: phantom 청소, orphan ROE 청산, **사용자 수동분(ORDI) 절대 미청산**.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.live.orphan_guard import OrphanGuard


class _Store:
    def __init__(self, held, bot_ordered):
        self._held = held            # {sid: [(sym, qty)]}
        self._bot = bot_ordered      # {sym: sid}
        self.synced = []
    def all_positions(self): return self._held
    def bot_ordered_symbols(self): return self._bot
    def force_sync_position(self, *, strategy_id, symbol, qty):
        self.synced.append((strategy_id, symbol, qty))


class _Adapter:
    def __init__(self, positions): self._pos = positions; self.closed = []
    async def get_protective_positions(self): return self._pos
    async def place_order(self, req):
        self.closed.append((req.symbol, req.side.value, req.qty, req.reduce_only))


def _pos(symbol, hold_side, qty, entry, mark, lev=10):
    return {"symbol": symbol, "hold_side": hold_side, "qty": Decimal(str(qty)),
            "entry": Decimal(str(entry)), "mark": Decimal(str(mark)),
            "leverage": Decimal(str(lev)), "upl": Decimal("0")}


def _guard(adapter, store):
    return OrphanGuard(adapter=adapter, position_store=store,
                       take_profit_roi=0.10, stop_loss_roi=0.05, default_leverage=10)


@pytest.mark.asyncio
async def test_phantom_cleared_multi_holder():
    # SNDK: store 2 holders, broker 없음 → 둘 다 0 정합
    store = _Store({"kst": [("SNDKUSDT", -0.1)], "wl": [("SNDKUSDT", -0.126)]}, {})
    a = _Adapter([])
    await _guard(a, store).check_once()
    cleared = {(sid, sym, q) for (sid, sym, q) in store.synced}
    assert ("kst", "SNDKUSDT", Decimal("0")) in cleared
    assert ("wl", "SNDKUSDT", Decimal("0")) in cleared
    assert not a.closed


@pytest.mark.asyncio
async def test_orphan_short_tp_closed():
    # BEAT 숏 entry100 mark98 → ROE=(100-98)/100*10=+20% ≥ TP10% → 청산(BUY)
    store = _Store({}, {"BEATUSDT": "kst"})
    a = _Adapter([_pos("BEATUSDT", "short", -50, 100, 98)])
    await _guard(a, store).check_once()
    assert a.closed == [("BEATUSDT", "BUY", Decimal("50"), True)]


@pytest.mark.asyncio
async def test_orphan_short_sl_closed():
    # entry100 mark100.6 → ROE=(100-100.6)/100*10=-6% ≤ -SL5% → 청산
    store = _Store({}, {"BEATUSDT": "kst"})
    a = _Adapter([_pos("BEATUSDT", "short", -50, 100, 100.6)])
    await _guard(a, store).check_once()
    assert len(a.closed) == 1 and a.closed[0][1] == "BUY"


@pytest.mark.asyncio
async def test_orphan_not_breaching_left_open():
    # mark99.9 → ROE=+1% → 미돌파 → 청산 안 함
    store = _Store({}, {"BEATUSDT": "kst"})
    a = _Adapter([_pos("BEATUSDT", "short", -50, 100, 99.9)])
    await _guard(a, store).check_once()
    assert not a.closed


@pytest.mark.asyncio
async def test_manual_position_never_touched():
    # ORDI: broker 에 있고 ROE 크게 돌파했지만 봇 주문 아님 → 절대 청산 안 함
    store = _Store({}, {})  # bot_ordered 비어있음
    a = _Adapter([_pos("ORDIUSDT", "long", 147, 100, 130)])  # ROE +300%
    await _guard(a, store).check_once()
    assert not a.closed


@pytest.mark.asyncio
async def test_attributed_position_not_touched():
    # BEAT 가 store 에 귀속돼 있으면(synthetic 담당) OrphanGuard 는 skip
    store = _Store({"kst": [("BEATUSDT", -50)]}, {"BEATUSDT": "kst"})
    a = _Adapter([_pos("BEATUSDT", "short", -50, 100, 98)])
    await _guard(a, store).check_once()
    assert not a.closed
    assert not store.synced  # 귀속분 = phantom 아님 → 손 안 댐


@pytest.mark.asyncio
async def test_orphan_long_tp_closed_sells():
    # 롱 orphan entry100 mark101 → ROE=+10% → 청산(SELL)
    store = _Store({}, {"XUSDT": "kst"})
    a = _Adapter([_pos("XUSDT", "long", 10, 100, 101)])
    await _guard(a, store).check_once()
    assert a.closed == [("XUSDT", "SELL", Decimal("10"), True)]
