from __future__ import annotations

from decimal import Decimal
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.brokers.base import OrderRequest, OrderAck, OrderType, HealthStatus
from src.brokers.errors import BrokerError
from src.brokers.router import OrderRouter
from src.execution.base import Side, TimeInForce
from src.ops.kill_switch import KillSwitch, KillSwitchTripped


def _make_req(emergency_exit: bool = False) -> OrderRequest:
    return OrderRequest(
        client_order_id="cid1",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=Decimal("0.001"),
        order_type=OrderType.MARKET,
        price=None,
        tif=TimeInForce.GTC,
        emergency_exit=emergency_exit,
    )


def _make_router(ks: KillSwitch) -> OrderRouter:
    from src.brokers.base import BrokerAdapter, Balance
    broker = MagicMock()
    broker.name = "test"
    broker.paper = True
    broker.place_order.return_value = OrderAck(
        broker_order_id="bo1", client_order_id="cid1",
        symbol="BTCUSDT", status="NEW", ts=datetime(2026, 4, 20, 9, 0),
    )
    broker.health_check.return_value = HealthStatus.OK
    broker.get_positions.return_value = []
    return OrderRouter(active=broker, kill_switch=ks)


def test_kill_switch_blocks_normal_order():
    ks = KillSwitch()
    ks.trip(reason="test", source="manual:test")
    router = _make_router(ks)

    with pytest.raises(KillSwitchTripped):
        router.place_order(_make_req(emergency_exit=False))


def test_kill_switch_allows_emergency_exit():
    ks = KillSwitch()
    ks.trip(reason="test", source="manual:test")
    router = _make_router(ks)

    # emergency_exit=True should bypass kill switch (liquidation path)
    ack = router.place_order(_make_req(emergency_exit=True))
    assert ack.status == "NEW"


def test_kill_switch_not_tripped_allows_normal_order():
    ks = KillSwitch()
    assert not ks.tripped
    router = _make_router(ks)
    ack = router.place_order(_make_req())
    assert ack.status == "NEW"


def test_kill_switch_trip_and_release():
    ks = KillSwitch()
    ks.trip(reason="test", source="auto:test")
    assert ks.tripped
    ks.release(operator="ops")
    assert not ks.tripped
    router = _make_router(ks)
    ack = router.place_order(_make_req())
    assert ack.status == "NEW"


def test_health_check_unhealthy_records_metric():
    from src.observability.metrics import Metrics
    ks = KillSwitch()
    broker = MagicMock()
    broker.name = "binance_futures"
    broker.paper = True
    broker.health_check.return_value = HealthStatus.DOWN
    broker.get_positions.return_value = []

    metrics = Metrics()
    router = OrderRouter(active=broker, kill_switch=ks, metrics=metrics)
    router.health_check()

    assert ks.tripped
    # risk_breach_total{rule="broker_unhealthy"} should have been incremented
    val = metrics.risk_breach_total.labels(rule="broker_unhealthy", severity="critical")._value.get()
    assert val >= 1
