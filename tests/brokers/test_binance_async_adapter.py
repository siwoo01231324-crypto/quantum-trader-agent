"""Unit tests for Binance async REST adapter (C3).

Network-zero: all HTTP is mocked via respx.
"""
from __future__ import annotations

import pytest
import respx
import httpx
from decimal import Decimal
from datetime import datetime, timezone

from src.brokers.base import (
    AsyncBrokerAdapter,
    Balance,
    HealthStatus,
    MarginType,
    OrderAck,
    OrderRequest,
    Position,
    PositionSide,
)
from src.brokers.base import OrderType
from src.execution.base import Side, TimeInForce
from src.brokers.binance.async_adapter import AsyncBinanceFuturesAdapter
from src.brokers.errors import BrokerClosedError, BrokerStartupError


BASE_URL = "https://fapi.binance.com"


def _make_adapter(**kwargs) -> AsyncBinanceFuturesAdapter:
    return AsyncBinanceFuturesAdapter(
        api_key="testkey",
        secret="testsecret",
        base_url=BASE_URL,
        **kwargs,
    )


def _order_request(symbol: str = "BTCUSDT") -> OrderRequest:
    return OrderRequest(
        client_order_id="abc123defabc123defabc123defabc123de",
        symbol=symbol,
        side=Side.BUY,
        qty=Decimal("0.001"),
        order_type=OrderType.MARKET,
        price=None,
        tif=TimeInForce.GTC,
    )


@pytest.fixture
def adapter():
    return _make_adapter()


# ── Protocol conformance ──────────────────────────────────────────────────────

def test_async_adapter_conforms_to_protocol(adapter):
    assert isinstance(adapter, AsyncBrokerAdapter)


# ── place_order ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_place_order_success(adapter):
    respx.get(f"{BASE_URL}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1700000000000})
    )
    respx.post(f"{BASE_URL}/fapi/v1/order").mock(
        return_value=httpx.Response(200, json={
            "orderId": 123456,
            "clientOrderId": "abc123defabc123defabc123defabc123de",
            "symbol": "BTCUSDT",
            "status": "FILLED",
            "origQty": "0.001",
            "price": "0",
            "avgPrice": "42000.0",
            "updateTime": 1700000001000,
        })
    )
    req = _order_request()
    ack = await adapter.place_order(req)
    assert ack.broker_order_id == "123456"
    assert ack.symbol == "BTCUSDT"
    assert ack.status == "FILLED"
    assert ack.price is None  # price=0 maps to None


@pytest.mark.asyncio
@respx.mock
async def test_place_order_rejected_by_kill_switch():
    class _KS:
        def assert_allow_order(self, liquidation: bool) -> None:
            raise RuntimeError("Kill switch active")

    adapter = _make_adapter(kill_switch=_KS())
    with pytest.raises(RuntimeError, match="Kill switch"):
        await adapter.place_order(_order_request())


@pytest.mark.asyncio
async def test_place_order_rejected_when_closing(adapter):
    adapter._closing = True
    with pytest.raises(BrokerClosedError):
        await adapter.place_order(_order_request())


# ── cancel_order ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_cancel_order_success(adapter):
    respx.get(f"{BASE_URL}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1700000000000})
    )
    respx.delete(f"{BASE_URL}/fapi/v1/order").mock(
        return_value=httpx.Response(200, json={
            "orderId": 999,
            "clientOrderId": "abc123defabc123defabc123defabc123de",
            "symbol": "BTCUSDT",
            "status": "CANCELED",
            "origQty": "0.001",
            "price": "0",
        })
    )
    await adapter.cancel_order(broker_order_id="999", symbol="BTCUSDT")


@pytest.mark.asyncio
@respx.mock
async def test_cancel_order_failure(adapter):
    respx.get(f"{BASE_URL}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1700000000000})
    )
    respx.delete(f"{BASE_URL}/fapi/v1/order").mock(
        return_value=httpx.Response(400, json={"code": -2011, "msg": "Unknown order sent."})
    )
    from src.brokers.errors import InvalidOrderError
    with pytest.raises(InvalidOrderError):
        await adapter.cancel_order(broker_order_id="999", symbol="BTCUSDT")


# ── get_order ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_order_success(adapter):
    respx.get(f"{BASE_URL}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1700000000000})
    )
    respx.get(f"{BASE_URL}/fapi/v1/order").mock(
        return_value=httpx.Response(200, json={
            "orderId": 555,
            "clientOrderId": "abc123defabc123defabc123defabc123de",
            "symbol": "ETHUSDT",
            "status": "NEW",
            "origQty": "0.01",
            "price": "2000",
            "avgPrice": "0",
            "updateTime": 1700000002000,
        })
    )
    ack = await adapter.get_order(broker_order_id="555", symbol="ETHUSDT")
    assert ack.broker_order_id == "555"
    assert ack.price == Decimal("2000")


# ── get_positions ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_positions_success(adapter):
    respx.get(f"{BASE_URL}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1700000000000})
    )
    respx.get(f"{BASE_URL}/fapi/v2/positionRisk").mock(
        return_value=httpx.Response(200, json=[
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.5",
                "entryPrice": "40000",
                "markPrice": "41000",
                "liquidationPrice": "35000",
                "leverage": "10",
                "marginType": "isolated",
                "positionSide": "LONG",
            },
            {
                "symbol": "ETHUSDT",
                "positionAmt": "0",
                "entryPrice": "0",
                "markPrice": "2000",
                "liquidationPrice": "0",
                "leverage": "5",
                "marginType": "cross",
                "positionSide": "BOTH",
            },
        ])
    )
    positions = await adapter.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "BTCUSDT"
    assert positions[0].qty == Decimal("0.5")


# ── get_balance ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_get_balance_success(adapter):
    respx.get(f"{BASE_URL}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1700000000000})
    )
    respx.get(f"{BASE_URL}/fapi/v2/balance").mock(
        return_value=httpx.Response(200, json=[
            {
                "asset": "USDT",
                "balance": "1000.00",
                "availableBalance": "800.00",
                "crossWalletBalance": "1000.00",
            }
        ])
    )
    balances = await adapter.get_balance()
    assert len(balances) == 1
    assert balances[0].asset == "USDT"
    assert balances[0].free == Decimal("800.00")
    assert balances[0].locked == Decimal("200.00")


# ── health_check ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_health_check_ok(adapter):
    respx.get(f"{BASE_URL}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1700000000000})
    )
    respx.get(f"{BASE_URL}/fapi/v1/ping").mock(
        return_value=httpx.Response(200, json={})
    )
    status = await adapter.health_check()
    assert status == HealthStatus.OK


@pytest.mark.asyncio
@respx.mock
async def test_health_check_down(adapter):
    respx.get(f"{BASE_URL}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1700000000000})
    )
    respx.get(f"{BASE_URL}/fapi/v1/ping").mock(
        return_value=httpx.Response(500, json={})
    )
    status = await adapter.health_check()
    assert status == HealthStatus.DOWN


# ── ensure_leverage ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_ensure_leverage_already_set(adapter):
    respx.get(f"{BASE_URL}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1700000000000})
    )
    respx.get(f"{BASE_URL}/fapi/v2/positionRisk").mock(
        return_value=httpx.Response(200, json=[{
            "symbol": "BTCUSDT",
            "positionAmt": "0",
            "entryPrice": "0",
            "markPrice": "40000",
            "liquidationPrice": "0",
            "leverage": "10",
            "marginType": "isolated",
            "positionSide": "BOTH",
        }])
    )
    # Should NOT call set_leverage (no POST to /fapi/v1/leverage)
    await adapter.ensure_leverage("BTCUSDT", 10)


@pytest.mark.asyncio
@respx.mock
async def test_ensure_leverage_needs_update(adapter):
    respx.get(f"{BASE_URL}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1700000000000})
    )
    respx.get(f"{BASE_URL}/fapi/v2/positionRisk").mock(
        return_value=httpx.Response(200, json=[{
            "symbol": "BTCUSDT",
            "positionAmt": "0",
            "entryPrice": "0",
            "markPrice": "40000",
            "liquidationPrice": "0",
            "leverage": "5",
            "marginType": "isolated",
            "positionSide": "BOTH",
        }])
    )
    respx.post(f"{BASE_URL}/fapi/v1/leverage").mock(
        return_value=httpx.Response(200, json={"leverage": 10, "symbol": "BTCUSDT"})
    )
    await adapter.ensure_leverage("BTCUSDT", 10)


# ── ensure_margin_type ────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_ensure_margin_type_already_set(adapter):
    respx.get(f"{BASE_URL}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1700000000000})
    )
    respx.get(f"{BASE_URL}/fapi/v2/positionRisk").mock(
        return_value=httpx.Response(200, json=[{
            "symbol": "BTCUSDT",
            "positionAmt": "0",
            "entryPrice": "0",
            "markPrice": "40000",
            "liquidationPrice": "0",
            "leverage": "10",
            "marginType": "ISOLATED",
            "positionSide": "BOTH",
        }])
    )
    await adapter.ensure_margin_type("BTCUSDT", MarginType.ISOLATED)


@pytest.mark.asyncio
@respx.mock
async def test_ensure_margin_type_needs_update(adapter):
    respx.get(f"{BASE_URL}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1700000000000})
    )
    respx.get(f"{BASE_URL}/fapi/v2/positionRisk").mock(
        return_value=httpx.Response(200, json=[{
            "symbol": "BTCUSDT",
            "positionAmt": "0",
            "entryPrice": "0",
            "markPrice": "40000",
            "liquidationPrice": "0",
            "leverage": "10",
            "marginType": "CROSSED",
            "positionSide": "BOTH",
        }])
    )
    respx.post(f"{BASE_URL}/fapi/v1/marginType").mock(
        return_value=httpx.Response(200, json={"msg": "success"})
    )
    await adapter.ensure_margin_type("BTCUSDT", MarginType.ISOLATED)


# ── aclose ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_aclose_idempotent(adapter):
    """aclose() must be safe to call multiple times."""
    await adapter.aclose()
    await adapter.aclose()  # second call must not raise


@pytest.mark.asyncio
async def test_aclose_sets_closing_flag(adapter):
    await adapter.aclose()
    assert adapter._closing is True
