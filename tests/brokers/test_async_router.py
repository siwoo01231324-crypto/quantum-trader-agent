"""Test Stage 2.2: AsyncOrderRouter — async parallel of sync OrderRouter."""
from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from prometheus_client import CollectorRegistry

from src.brokers.async_router import AsyncOrderRouter
from src.brokers.base import (
    AsyncBrokerAdapter,
    Balance,
    HealthStatus,
    OrderAck,
    OrderRequest,
    OrderType,
    Position,
    PositionSide,
)
from src.brokers.errors import RateLimitError
from src.brokers.kis.rate_limiter import KisRateLimiter
from src.execution.base import Side, TimeInForce
from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch, KillSwitchTripped


def _make_async_broker(name: str = "kis_paper", paper: bool = True) -> MagicMock:
    broker = MagicMock(spec=AsyncBrokerAdapter)
    broker.name = name
    broker.paper = paper
    broker.place_order = AsyncMock(
        return_value=OrderAck(
            broker_order_id="bo1",
            client_order_id="cid1",
            symbol="005930",
            status="NEW",
            ts=datetime(2026, 4, 26, 9, 0),
        )
    )
    broker.cancel_order = AsyncMock()
    broker.get_order = AsyncMock()
    broker.get_positions = AsyncMock(return_value=[])
    broker.get_balance = AsyncMock(
        return_value=[Balance(asset="KRW", free=Decimal("1000000"), locked=Decimal("0"))]
    )
    broker.health_check = AsyncMock(return_value=HealthStatus.OK)
    broker.aclose = AsyncMock()
    return broker


def _make_order_req(symbol: str = "005930") -> OrderRequest:
    return OrderRequest(
        client_order_id="cid1",
        symbol=symbol,
        side=Side.BUY,
        qty=Decimal("10"),
        order_type=OrderType.MARKET,
        price=None,
        tif=TimeInForce.GTC,
    )


def _make_metrics() -> Metrics:
    return Metrics(registry=CollectorRegistry())


# ---------------------------------------------------------------------------
# 1. kill-switch tripped → KillSwitchTripped raised before broker call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_place_order_blocked_when_kill_switch_tripped():
    broker = _make_async_broker()
    ks = KillSwitch()
    ks.trip(reason="test", source="manual:test")
    router = AsyncOrderRouter(active=broker, kill_switch=ks)
    with pytest.raises(KillSwitchTripped):
        await router.place_order(_make_order_req())
    broker.place_order.assert_not_called()


# ---------------------------------------------------------------------------
# 2. place_order → metrics emitted (orders_total + orders_placed_total)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_place_order_emits_orders_total_and_placed_total():
    broker = _make_async_broker()
    m = _make_metrics()
    router = AsyncOrderRouter(active=broker, metrics=m)
    await router.place_order(_make_order_req())

    # orders_total
    orders_total = sum(
        s.value
        for metric in m.orders_total.collect()
        for s in metric.samples
        if s.labels.get("broker") == "kis_paper"
    )
    assert orders_total >= 1

    # orders_placed_total (NEW ack)
    placed_total = sum(
        s.value
        for metric in m.orders_placed_total.collect()
        for s in metric.samples
        if s.labels.get("strategy") == "unknown"
    )
    assert placed_total >= 1


# ---------------------------------------------------------------------------
# 3. swap_active: env flag gate + cancel_all_open hasattr-check + snapshot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_swap_active_requires_env_flag(monkeypatch):
    broker = _make_async_broker()
    new_broker = _make_async_broker(name="kis_live", paper=False)
    monkeypatch.setenv("BROKER_ROUTER_ENABLED", "false")
    router = AsyncOrderRouter(active=broker)
    with pytest.raises(RuntimeError, match="BROKER_ROUTER_ENABLED"):
        await router.swap_active(new_broker)


@pytest.mark.asyncio
async def test_swap_active_snapshots_and_switches(monkeypatch):
    old_broker = _make_async_broker(name="old")
    pos = Position(
        symbol="005930", side=PositionSide.LONG,
        qty=Decimal("10"), entry_price=Decimal("70000")
    )
    old_broker.get_positions = AsyncMock(return_value=[pos])
    old_broker.cancel_all_open = AsyncMock()
    new_broker = _make_async_broker(name="new")
    monkeypatch.setenv("BROKER_ROUTER_ENABLED", "true")

    router = AsyncOrderRouter(active=old_broker)
    snapshot = await router.swap_active(new_broker)

    old_broker.get_positions.assert_called()
    old_broker.cancel_all_open.assert_called_once()
    assert router.active is new_broker
    assert len(snapshot) == 1
    assert snapshot[0].symbol == "005930"


@pytest.mark.asyncio
async def test_swap_active_no_cancel_all_open_skips_gracefully(monkeypatch):
    """Broker without cancel_all_open — swap must still succeed."""
    old_broker = _make_async_broker(name="old")
    # Remove cancel_all_open if present
    if hasattr(old_broker, "cancel_all_open"):
        del old_broker.cancel_all_open
    new_broker = _make_async_broker(name="new")
    monkeypatch.setenv("BROKER_ROUTER_ENABLED", "true")

    router = AsyncOrderRouter(active=old_broker)
    snapshot = await router.swap_active(new_broker)
    assert router.active is new_broker


# ---------------------------------------------------------------------------
# 4. health_check DOWN → trips kill_switch + increments risk_breach_total
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_check_down_trips_kill_switch():
    broker = _make_async_broker()
    broker.health_check = AsyncMock(return_value=HealthStatus.DOWN)
    ks = KillSwitch()
    m = _make_metrics()
    router = AsyncOrderRouter(active=broker, kill_switch=ks, metrics=m)

    status = await router.health_check()

    assert status == HealthStatus.DOWN
    assert ks.tripped

    breach_total = sum(
        s.value
        for metric in m.risk_breach_total.collect()
        for s in metric.samples
        if s.labels.get("rule") == "broker_unhealthy"
    )
    assert breach_total >= 1


# ---------------------------------------------------------------------------
# 5. multiple brokers registered (paper + live)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_broker_registration(monkeypatch):
    paper_broker = _make_async_broker(name="kis_paper", paper=True)
    live_broker = _make_async_broker(name="kis_live", paper=False)
    monkeypatch.setenv("BROKER_ROUTER_ENABLED", "true")

    router = AsyncOrderRouter(active=paper_broker)
    await router.swap_active(live_broker)
    assert router.active.name == "kis_live"


# ---------------------------------------------------------------------------
# 6. rate-limit hit → RateLimitError + metric incremented
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_error_increments_metric():
    broker = _make_async_broker()
    m = _make_metrics()
    # burst=1 so second call raises
    limiter = KisRateLimiter(burst=1, refill_rate=1.0, scope="paper", metrics=m)
    router = AsyncOrderRouter(active=broker, metrics=m, rate_limiter=limiter)

    await router.place_order(_make_order_req())  # consumes burst
    with pytest.raises(RateLimitError):
        await router.place_order(_make_order_req())

    hit_total = sum(
        s.value
        for metric in m.broker_rate_limit_hit_total.collect()
        for s in metric.samples
        if s.labels.get("broker") == "kis"
    )
    assert hit_total >= 1


# ---------------------------------------------------------------------------
# 7. Protocol compliance: AsyncOrderRouter accepts AsyncBrokerAdapter
# ---------------------------------------------------------------------------

def test_async_broker_adapter_protocol_runtime_check():
    broker = _make_async_broker()
    assert isinstance(broker, AsyncBrokerAdapter)
