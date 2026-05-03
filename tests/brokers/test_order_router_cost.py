"""Tests for ExecutionCostEstimator and cost-based dynamic routing in OrderRouter."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.brokers.base import (
    AsyncBrokerAdapter,
    BrokerAdapter,
    HealthStatus,
    OrderAck,
    OrderRequest,
    OrderType,
)
from src.brokers.async_router import AsyncOrderRouter
from src.brokers.router import ExecutionCostEstimator, OrderRouter
from src.brokers.types import BrokerFill
from src.execution.base import Side, TimeInForce


# ── helpers ───────────────────────────────────────────────────────────────────

def _fill(price: str, fee: str, qty: str = "1") -> BrokerFill:
    return BrokerFill(
        parent_id="p1",
        broker_order_id="bo1",
        client_order_id="c1",
        trade_id="t1",
        qty=Decimal(qty),
        price=Decimal(price),
        fee=Decimal(fee),
        fee_asset="USDT",
        ts=datetime(2026, 4, 27, 9, 0),
        is_maker=False,
    )


def _req() -> OrderRequest:
    return OrderRequest(
        client_order_id="cid1",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=Decimal("0.001"),
        order_type=OrderType.MARKET,
        price=None,
        tif=TimeInForce.GTC,
    )


def _mock_broker(name: str) -> MagicMock:
    b = MagicMock(spec=BrokerAdapter)
    b.name = name
    b.paper = True
    b.health_check.return_value = HealthStatus.OK
    b.get_positions.return_value = []
    b.place_order.return_value = OrderAck(
        broker_order_id="bo1",
        client_order_id="cid1",
        symbol="BTCUSDT",
        status="NEW",
        ts=datetime(2026, 4, 27, 9, 0),
    )
    return b


def _mock_async_broker(name: str) -> MagicMock:
    b = MagicMock(spec=AsyncBrokerAdapter)
    b.name = name
    b.paper = True
    b.place_order = AsyncMock(return_value=OrderAck(
        broker_order_id="bo1",
        client_order_id="cid1",
        symbol="BTCUSDT",
        status="NEW",
        ts=datetime(2026, 4, 27, 9, 0),
    ))
    b.health_check = AsyncMock(return_value=HealthStatus.OK)
    b.get_positions = AsyncMock(return_value=[])
    b.aclose = AsyncMock()
    return b


# ── ExecutionCostEstimator unit tests ─────────────────────────────────────────

def test_cost_score_zero_for_no_samples():
    est = ExecutionCostEstimator()
    assert est.cost_score("binance") == Decimal("0")


def test_cost_score_fee_only():
    est = ExecutionCostEstimator()
    # fill at exactly mid price → zero slippage; fee = 0.001 USDT on 1*100 = 0.00001 ratio
    est.record_fill("binance", _fill("100", "0.001"), mid_price=Decimal("100"))
    score = est.cost_score("binance")
    # slippage = 0, fee_ratio = 0.001 / 100 = 0.00001
    assert score == Decimal("0.00001")


def test_cost_score_slippage_positive():
    est = ExecutionCostEstimator()
    # fill at 101, mid=100 → slippage = 0.01; fee = 0.5/101 ≈ tiny
    est.record_fill("binance", _fill("101", "0.5", "1"), mid_price=Decimal("100"))
    score = est.cost_score("binance")
    assert score > Decimal("0.01")


def test_best_broker_selects_lower_score():
    est = ExecutionCostEstimator()
    mid = Decimal("100")
    # binance: fill at 101 (high slippage)
    est.record_fill("binance", _fill("101", "0.1", "1"), mid_price=mid)
    # kis: fill at exactly mid (no slippage, tiny fee)
    est.record_fill("kis", _fill("100", "0.01", "1"), mid_price=mid)
    assert est.best_broker(["binance", "kis"]) == "kis"


def test_best_broker_prefers_no_sample_over_positive_cost():
    est = ExecutionCostEstimator()
    mid = Decimal("100")
    est.record_fill("expensive", _fill("110", "1", "1"), mid_price=mid)
    # "new_broker" has no samples → score = 0 → cheaper
    assert est.best_broker(["expensive", "new_broker"]) == "new_broker"


def test_window_limits_samples():
    est = ExecutionCostEstimator(window=3)
    mid = Decimal("100")
    # push 3 cheap fills then 10 expensive fills — window should cap at 3 expensive
    for _ in range(3):
        est.record_fill("b", _fill("100", "0.001"), mid_price=mid)
    for _ in range(10):
        est.record_fill("b", _fill("110", "1"), mid_price=mid)
    # only last 3 (all expensive) remain
    score = est.cost_score("b")
    assert score > Decimal("0.05")


def test_record_fill_ignores_zero_mid_price():
    est = ExecutionCostEstimator()
    est.record_fill("b", _fill("100", "0.1"), mid_price=Decimal("0"))
    assert est.cost_score("b") == Decimal("0")


# ── OrderRouter integration tests ─────────────────────────────────────────────

def test_router_single_broker_no_cost_routing():
    broker = _mock_broker("binance")
    router = OrderRouter(active=broker)
    router.place_order(_req())
    broker.place_order.assert_called_once()


def test_router_routes_to_cheaper_broker():
    cheap = _mock_broker("kis")
    expensive = _mock_broker("binance")
    mid = Decimal("100")

    est = ExecutionCostEstimator()
    est.record_fill("binance", _fill("101", "0.5"), mid_price=mid)
    est.record_fill("kis", _fill("100", "0.01"), mid_price=mid)

    router = OrderRouter(active=expensive, cost_estimator=est)
    router.register_broker(cheap)

    router.place_order(_req())
    cheap.place_order.assert_called_once()
    expensive.place_order.assert_not_called()


def test_router_force_broker_overrides_cost():
    cheap = _mock_broker("kis")
    expensive = _mock_broker("binance")
    mid = Decimal("100")

    est = ExecutionCostEstimator()
    est.record_fill("binance", _fill("101", "0.5"), mid_price=mid)
    est.record_fill("kis", _fill("100", "0.01"), mid_price=mid)

    router = OrderRouter(active=expensive, cost_estimator=est)
    router.register_broker(cheap)

    # force to binance even though kis is cheaper
    router.place_order(_req(), force_broker="binance")
    expensive.place_order.assert_called_once()
    cheap.place_order.assert_not_called()


def test_router_force_broker_unknown_raises():
    broker = _mock_broker("binance")
    router = OrderRouter(active=broker)
    with pytest.raises(KeyError, match="unknown"):
        router.place_order(_req(), force_broker="unknown")


def test_router_register_broker_appears_in_selection():
    b1 = _mock_broker("b1")
    b2 = _mock_broker("b2")
    router = OrderRouter(active=b1)
    router.register_broker(b2)
    assert "b2" in router._brokers


def test_router_equal_scores_returns_consistent_result():
    b1 = _mock_broker("b1")
    b2 = _mock_broker("b2")
    est = ExecutionCostEstimator()
    # no fills → both score 0 → min() picks first alphabetically
    router = OrderRouter(active=b1, cost_estimator=est)
    router.register_broker(b2)
    router.place_order(_req())
    # either broker is valid; just assert no exception and one was called
    assert b1.place_order.call_count + b2.place_order.call_count == 1


# ── AsyncOrderRouter integration tests ───────────────────────────────────────

@pytest.mark.asyncio
async def test_async_router_routes_to_cheaper_broker():
    cheap = _mock_async_broker("kis")
    expensive = _mock_async_broker("binance")
    mid = Decimal("100")

    est = ExecutionCostEstimator()
    est.record_fill("binance", _fill("101", "0.5"), mid_price=mid)
    est.record_fill("kis", _fill("100", "0.01"), mid_price=mid)

    router = AsyncOrderRouter(active=expensive, cost_estimator=est)
    router.register_broker(cheap)

    await router.place_order(_req())
    cheap.place_order.assert_awaited_once()
    expensive.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_router_force_broker_overrides_cost():
    cheap = _mock_async_broker("kis")
    expensive = _mock_async_broker("binance")
    mid = Decimal("100")

    est = ExecutionCostEstimator()
    est.record_fill("binance", _fill("101", "0.5"), mid_price=mid)
    est.record_fill("kis", _fill("100", "0.01"), mid_price=mid)

    router = AsyncOrderRouter(active=expensive, cost_estimator=est)
    router.register_broker(cheap)

    await router.place_order(_req(), force_broker="binance")
    expensive.place_order.assert_awaited_once()
    cheap.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_router_single_broker_still_works():
    broker = _mock_async_broker("binance")
    router = AsyncOrderRouter(active=broker)
    await router.place_order(_req())
    broker.place_order.assert_awaited_once()
