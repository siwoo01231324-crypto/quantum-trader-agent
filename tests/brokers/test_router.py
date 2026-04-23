from __future__ import annotations

from decimal import Decimal
from datetime import datetime
from unittest.mock import MagicMock, patch
import os

import pytest

from src.brokers.base import (
    BrokerAdapter, OrderRequest, OrderAck, Position, Balance,
    OrderType, PositionSide, MarginType, HealthStatus,
)
from src.brokers.errors import ConfigurationError
from src.brokers.router import OrderRouter
from src.execution.base import Side, TimeInForce


def _make_mock_broker(name: str = "test", paper: bool = True) -> MagicMock:
    broker = MagicMock(spec=BrokerAdapter)
    broker.name = name
    broker.paper = paper
    broker.health_check.return_value = HealthStatus.OK
    broker.get_positions.return_value = []
    broker.get_balance.return_value = [Balance(asset="USDT", free=Decimal("1000"), locked=Decimal("0"))]
    return broker


def _make_order_req() -> OrderRequest:
    return OrderRequest(
        client_order_id="cid1",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=Decimal("0.001"),
        order_type=OrderType.MARKET,
        price=None,
        tif=TimeInForce.GTC,
    )


def test_router_delegates_place_order_to_active_broker():
    broker = _make_mock_broker("binance_futures")
    broker.place_order.return_value = OrderAck(
        broker_order_id="bo1", client_order_id="cid1",
        symbol="BTCUSDT", status="NEW", ts=datetime(2026, 4, 20, 9, 0),
    )
    router = OrderRouter(active=broker)
    req = _make_order_req()
    ack = router.place_order(req)
    broker.place_order.assert_called_once_with(req)
    assert ack.status == "NEW"


def test_router_delegates_cancel_order():
    broker = _make_mock_broker()
    router = OrderRouter(active=broker)
    router.cancel_order(broker_order_id="bo1", symbol="BTCUSDT")
    broker.cancel_order.assert_called_once()


def test_router_delegates_get_positions():
    broker = _make_mock_broker()
    router = OrderRouter(active=broker)
    router.get_positions()
    broker.get_positions.assert_called_once()


def test_router_delegates_get_balance():
    broker = _make_mock_broker()
    router = OrderRouter(active=broker)
    router.get_balance()
    broker.get_balance.assert_called_once()


def test_swap_active_calls_cancel_all_and_snapshot(monkeypatch):
    old_broker = _make_mock_broker("old")
    new_broker = _make_mock_broker("new")
    old_broker.get_positions.return_value = [
        Position(symbol="BTCUSDT", side=PositionSide.LONG, qty=Decimal("0.01"),
                 entry_price=Decimal("50000"))
    ]

    router = OrderRouter(active=old_broker)
    monkeypatch.setenv("BROKER_ROUTER_ENABLED", "true")

    snapshot = router.swap_active(new_broker)

    old_broker.cancel_order  # cancel_all called internally
    old_broker.get_positions.assert_called()
    assert router.active is new_broker
    assert len(snapshot) == 1
    assert snapshot[0].symbol == "BTCUSDT"


def test_swap_requires_router_enabled_flag(monkeypatch):
    old_broker = _make_mock_broker("old")
    new_broker = _make_mock_broker("new")
    router = OrderRouter(active=old_broker)
    monkeypatch.setenv("BROKER_ROUTER_ENABLED", "false")

    with pytest.raises(Exception, match="BROKER_ROUTER_ENABLED"):
        router.swap_active(new_broker)


def test_router_health_check_delegates():
    broker = _make_mock_broker()
    broker.health_check.return_value = HealthStatus.OK
    router = OrderRouter(active=broker)
    status = router.health_check()
    assert status == HealthStatus.OK


def test_router_health_check_down_trips_kill_switch():
    from src.ops.kill_switch import KillSwitch
    broker = _make_mock_broker()
    broker.health_check.return_value = HealthStatus.DOWN
    ks = KillSwitch()
    router = OrderRouter(active=broker, kill_switch=ks)
    router.health_check()
    assert ks.tripped


def test_missing_secrets_raises_configuration_error(monkeypatch):
    monkeypatch.delenv("HANTOO_FAKE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_DEMO_API_KEY", raising=False)
    with pytest.raises(ConfigurationError):
        from src.brokers import config as cfg_mod
        import importlib
        importlib.reload(cfg_mod)
        cfg_mod.load_broker_config()
