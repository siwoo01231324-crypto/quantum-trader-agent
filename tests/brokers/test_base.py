from __future__ import annotations

from decimal import Decimal
from datetime import datetime

import pytest

from src.brokers.base import (
    BrokerAdapter,
    OrderRequest,
    OrderAck,
    Position,
    Balance,
    OrderType,
    PositionSide,
    MarginType,
    HealthStatus,
)
from src.execution.base import Side, TimeInForce


def test_order_type_values():
    assert OrderType.MARKET == "MARKET"
    assert OrderType.LIMIT == "LIMIT"


def test_position_side_values():
    assert PositionSide.BOTH == "BOTH"
    assert PositionSide.LONG == "LONG"
    assert PositionSide.SHORT == "SHORT"


def test_margin_type_values():
    assert MarginType.ISOLATED == "ISOLATED"
    assert MarginType.CROSSED == "CROSSED"


def test_health_status_enum():
    assert HealthStatus.OK == "OK"
    assert HealthStatus.DEGRADED == "DEGRADED"
    assert HealthStatus.DOWN == "DOWN"


def test_order_request_defaults():
    req = OrderRequest(
        client_order_id="cid1",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=Decimal("0.001"),
        order_type=OrderType.MARKET,
        price=None,
        tif=TimeInForce.GTC,
    )
    assert req.position_side == PositionSide.BOTH
    assert req.reduce_only is False
    assert req.close_position is False
    assert req.emergency_exit is False


def test_order_request_limit():
    req = OrderRequest(
        client_order_id="cid2",
        symbol="BTCUSDT",
        side=Side.SELL,
        qty=Decimal("1.0"),
        order_type=OrderType.LIMIT,
        price=Decimal("50000"),
        tif=TimeInForce.GTX,
    )
    assert req.price == Decimal("50000")
    assert req.order_type == OrderType.LIMIT


def test_order_ack_fields():
    ack = OrderAck(
        broker_order_id="bo1",
        client_order_id="co1",
        symbol="BTCUSDT",
        status="NEW",
        ts=datetime(2026, 4, 20, 9, 0),
    )
    assert ack.broker_order_id == "bo1"
    assert ack.status == "NEW"


def test_position_fields():
    pos = Position(
        symbol="BTCUSDT",
        side=PositionSide.LONG,
        qty=Decimal("0.01"),
        entry_price=Decimal("50000"),
        liquidation_price=Decimal("45000"),
        margin_ratio=Decimal("0.05"),
    )
    assert pos.symbol == "BTCUSDT"
    assert pos.margin_ratio == Decimal("0.05")


def test_balance_fields():
    bal = Balance(asset="USDT", free=Decimal("1000"), locked=Decimal("100"))
    assert bal.asset == "USDT"
    assert bal.free == Decimal("1000")


def test_broker_adapter_is_runtime_checkable():
    assert hasattr(BrokerAdapter, "__protocol_attrs__") or hasattr(BrokerAdapter, "_is_protocol")
    # runtime_checkable check: a plain object does not satisfy protocol
    class NotABroker:
        pass
    assert not isinstance(NotABroker(), BrokerAdapter)


def test_broker_adapter_protocol_methods():
    required = {
        "place_order", "cancel_order", "get_order", "get_positions",
        "get_balance", "stream_fills", "ensure_leverage",
        "ensure_margin_type", "ensure_position_mode", "health_check",
    }
    for method in required:
        assert hasattr(BrokerAdapter, method), f"Missing: {method}"
