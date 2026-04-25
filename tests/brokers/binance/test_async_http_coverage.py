"""Edge / error / verb coverage tests for AsyncBinanceFuturesClient.

Targets uncovered branches in src/brokers/binance/async_http.py:
- PUT/DELETE verbs (extend_listen_key, delete_listen_key, cancel_order)
- Error response path (non-2xx → map_error)
- Timestamp error retry path
- ValueError for missing broker_order_id + client_order_id
- Unsupported HTTP method
- listen_key lifecycle (issue/extend/delete)
- get_order / get_open_orders / get_position_risk / get_balance / set_leverage / set_margin_type / get/set_position_mode
"""
from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from src.brokers.async_rate_limiter import AsyncBinanceRateLimiter
from src.brokers.base import MarginType, OrderRequest, OrderType, PositionSide
from src.brokers.binance.async_http import AsyncBinanceFuturesClient
from src.brokers.errors import TimestampError, ValidationError
from src.execution.base import Side, TimeInForce


def _make_client(transport: httpx.MockTransport) -> AsyncBinanceFuturesClient:
    rl = AsyncBinanceRateLimiter()
    client = AsyncBinanceFuturesClient(
        api_key="k", secret="s", base_url="https://fapi.binance.test", rate_limiter=rl
    )
    # Replace internal http with mocked transport
    client._client = httpx.AsyncClient(
        transport=transport,
        trust_env=False,
        headers={"X-MBX-APIKEY": "k"},
        timeout=10.0,
    )
    client._last_sync = 9999999.0  # skip time sync
    return client


@pytest.mark.asyncio
async def test_issue_extend_delete_listen_key():
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json={"listenKey": "lkABC123"})
        if request.method == "PUT":
            return httpx.Response(200, json={})
        if request.method == "DELETE":
            return httpx.Response(200, json={})
        return httpx.Response(500)

    client = _make_client(httpx.MockTransport(handler))
    key = await client.issue_listen_key()
    assert key == "lkABC123"
    await client.extend_listen_key(key)
    await client.delete_listen_key(key)
    await client.aclose()

    verbs = [c[0] for c in calls]
    assert verbs == ["POST", "PUT", "DELETE"]


@pytest.mark.asyncio
async def test_error_response_maps_to_broker_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": -2014, "msg": "API-key format invalid."})

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(Exception):  # map_error returns a BrokerError subclass
        await client.ping()
    await client.aclose()


@pytest.mark.asyncio
async def test_timestamp_error_retries_once_then_succeeds():
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if request.url.path == "/fapi/v1/time":
            return httpx.Response(200, json={"serverTime": 1700000000000})
        if call_count == 1 or (call_count == 2 and request.url.path != "/fapi/v1/time"):
            # First order request: return timestamp error
            if request.url.path == "/fapi/v1/order":
                return httpx.Response(400, json={"code": -1021, "msg": "Timestamp outside recvWindow"})
        # Subsequent success
        return httpx.Response(200, json={
            "orderId": 1, "clientOrderId": "c1", "symbol": "BTCUSDT",
            "status": "NEW", "updateTime": 1700000000000,
            "origQty": "1.0", "price": "50000.0",
        })

    client = _make_client(httpx.MockTransport(handler))
    req = OrderRequest(
        client_order_id="c1",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=Decimal("1.0"),
        order_type=OrderType.LIMIT,
        price=Decimal("50000"),
        tif=TimeInForce.GTC,
        position_side=PositionSide.BOTH,
    )
    # Will attempt, get timestamp error, resync, retry
    resp = await client.place_order(req, "c1")
    assert resp.orderId == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_cancel_order_requires_id():
    client = _make_client(httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ValueError):
        await client.cancel_order("BTCUSDT")
    await client.aclose()


@pytest.mark.asyncio
async def test_get_order_requires_id():
    client = _make_client(httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ValueError):
        await client.get_order("BTCUSDT")
    await client.aclose()


@pytest.mark.asyncio
async def test_unsupported_method_raises():
    client = _make_client(httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ValueError, match="Unsupported HTTP method"):
        await client._request("PATCH", "/fapi/v1/ping", signed=False)
    await client.aclose()


@pytest.mark.asyncio
async def test_cancel_by_broker_and_by_client_id():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={
            "orderId": 1, "clientOrderId": "c", "symbol": "BTCUSDT",
            "status": "CANCELED", "origQty": "1", "price": "0",
        })

    client = _make_client(httpx.MockTransport(handler))
    await client.cancel_order("BTCUSDT", broker_order_id="123")
    await client.cancel_order("BTCUSDT", client_order_id="c1")
    await client.aclose()
    # Verify orderId vs origClientOrderId appeared in query strings
    qs0 = str(captured[0].url)
    qs1 = str(captured[1].url)
    assert "orderId=123" in qs0
    assert "origClientOrderId=c1" in qs1


@pytest.mark.asyncio
async def test_get_endpoints_coverage():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/fapi/v1/order":
            return httpx.Response(200, json={
                "orderId": 1, "clientOrderId": "c", "symbol": "BTCUSDT",
                "status": "NEW", "origQty": "1", "price": "50000",
                "updateTime": 1700000000000,
            })
        if path == "/fapi/v1/openOrders":
            return httpx.Response(200, json=[])
        if path == "/fapi/v2/positionRisk":
            return httpx.Response(200, json=[])
        if path == "/fapi/v2/balance":
            return httpx.Response(200, json=[])
        if path == "/fapi/v1/leverage":
            return httpx.Response(200, json={})
        if path == "/fapi/v1/marginType":
            return httpx.Response(200, json={})
        if path == "/fapi/v1/positionSide/dual":
            return httpx.Response(200, json={"dualSidePosition": False})
        return httpx.Response(500)

    client = _make_client(httpx.MockTransport(handler))
    resp = await client.get_order("BTCUSDT", broker_order_id="1")
    assert resp.orderId == 1
    assert await client.get_open_orders("BTCUSDT") == []
    assert await client.get_position_risk("BTCUSDT") == []
    assert await client.get_balance() == []
    await client.set_leverage("BTCUSDT", 10)
    await client.set_margin_type("BTCUSDT", MarginType.ISOLATED)
    mode = await client.get_position_mode()
    assert mode is False
    await client.set_position_mode(hedge=True)
    await client.aclose()


@pytest.mark.asyncio
async def test_place_order_reduce_only_in_hedge_raises():
    client = _make_client(httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    req = OrderRequest(
        client_order_id="c",
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=Decimal("1"),
        order_type=OrderType.MARKET,
        price=None,
        tif=TimeInForce.GTC,
        position_side=PositionSide.LONG,
        reduce_only=True,
    )
    with pytest.raises(ValidationError):
        await client.place_order(req, "c")
    await client.aclose()


@pytest.mark.asyncio
async def test_error_response_non_json_body():
    """Server returns non-JSON body in error response — should fallback to resp.text."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="<html>Internal Server Error</html>")

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(Exception):
        await client.ping()
    await client.aclose()
