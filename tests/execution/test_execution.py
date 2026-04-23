from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.execution import (
    ChildOrder,
    KRXSingleAuctionHandler,
    LimitAlgo,
    MarketAlgo,
    MarketState,
    ParentOrder,
    Side,
    SingleAuctionPolicy,
    TWAPAlgo,
    Tick,
    TimeInForce,
    VWAPAlgo,
)


def _tick(ts: datetime, last: float = 100.0, volume: int = 1000) -> Tick:
    return Tick(symbol="005930", bid=last - 0.5, ask=last + 0.5, last=last, volume=volume, ts=ts)


def _state(ts: datetime, **kwargs) -> MarketState:
    return MarketState(tick=_tick(ts), **kwargs)


class MockMatchingEngine:
    """Deterministic mock: fully fills any child order at the tick's last price."""

    def __init__(self) -> None:
        self.fills: list[ChildOrder] = []

    def submit(self, orders: list[ChildOrder]) -> None:
        self.fills.extend(orders)


def test_market_algo_emits_single_market_order():
    algo = MarketAlgo()
    parent = ParentOrder("p1", "005930", Side.BUY, 100)
    out = algo.plan(parent, _state(datetime(2026, 4, 13, 10, 0)))
    assert len(out) == 1
    assert out[0].price is None
    assert out[0].qty == 100
    # second plan call returns nothing
    assert algo.plan(parent, _state(datetime(2026, 4, 13, 10, 1))) == []


def test_limit_algo_uses_configured_price():
    algo = LimitAlgo(price=99.5, tif=TimeInForce.DAY)
    parent = ParentOrder("p1", "005930", Side.SELL, 50)
    out = algo.plan(parent, _state(datetime(2026, 4, 13, 10, 0)))
    assert out[0].price == 99.5
    assert out[0].tif == TimeInForce.DAY


def test_twap_emits_slices_over_time():
    algo = TWAPAlgo(duration=timedelta(minutes=10), slice_count=5)
    parent = ParentOrder("p1", "005930", Side.BUY, 100)
    start = datetime(2026, 4, 13, 10, 0)
    out0 = algo.plan(parent, _state(start))
    assert len(out0) == 1
    assert out0[0].qty == 20
    # advance halfway: should emit slice 2 and 3
    out1 = algo.on_market_tick(_tick(start + timedelta(minutes=5)))
    assert sum(o.qty for o in out1) == 40
    # finish
    out2 = algo.on_market_tick(_tick(start + timedelta(minutes=11)))
    total = 20 + sum(o.qty for o in out1) + sum(o.qty for o in out2)
    assert total == 100


def test_twap_invalid_slice_count():
    with pytest.raises(ValueError):
        TWAPAlgo(duration=timedelta(minutes=1), slice_count=0)


def test_vwap_distributes_qty_by_profile():
    profile = [1.0, 2.0, 1.0]
    algo = VWAPAlgo(volume_profile=profile, participation_rate=0.5)
    parent = ParentOrder("p1", "005930", Side.BUY, 100)
    start = datetime(2026, 4, 13, 10, 0)
    out: list[ChildOrder] = []
    out += algo.plan(parent, _state(start))
    out += algo.on_market_tick(_tick(start + timedelta(minutes=1)))
    out += algo.on_market_tick(_tick(start + timedelta(minutes=2)))
    assert sum(o.qty for o in out) == 100
    assert len(out) == 3


def test_vwap_invalid_inputs():
    with pytest.raises(ValueError):
        VWAPAlgo(volume_profile=[], participation_rate=0.1)
    with pytest.raises(ValueError):
        VWAPAlgo(volume_profile=[1.0], participation_rate=0)


def test_krx_handler_wait_policy_queues_during_auction():
    h = KRXSingleAuctionHandler(SingleAuctionPolicy.WAIT)
    ts = datetime(2026, 4, 13, 8, 45)
    order = ChildOrder("p1", "005930", Side.BUY, 10, price=None)
    out = h.filter([order], _state(ts, in_single_auction=True))
    assert out == []
    assert h.queued == 1
    # resume continuous trading -> flush
    out2 = h.filter([], _state(ts + timedelta(minutes=20), in_single_auction=False))
    assert len(out2) == 1


def test_krx_handler_participate_rewrites_to_limit():
    h = KRXSingleAuctionHandler(SingleAuctionPolicy.PARTICIPATE_AT_REFERENCE)
    ts = datetime(2026, 4, 13, 8, 45)
    order = ChildOrder("p1", "005930", Side.BUY, 10, price=None)
    state = _state(ts, in_single_auction=True)
    out = h.filter([order], state)
    assert len(out) == 1
    assert out[0].price == state.tick.last


def test_krx_handler_cancel_drops_orders_during_halt():
    h = KRXSingleAuctionHandler(SingleAuctionPolicy.CANCEL)
    ts = datetime(2026, 4, 13, 11, 0)
    order = ChildOrder("p1", "005930", Side.BUY, 10, price=None)
    out = h.filter([order], _state(ts, halted=True))
    assert out == []
    assert h.queued == 0


def test_mock_matching_engine_collects_fills():
    eng = MockMatchingEngine()
    algo = MarketAlgo()
    parent = ParentOrder("p1", "005930", Side.BUY, 25)
    eng.submit(algo.plan(parent, _state(datetime(2026, 4, 13, 10, 0))))
    assert len(eng.fills) == 1
    assert eng.fills[0].qty == 25
