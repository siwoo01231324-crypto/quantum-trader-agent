"""KIS async adapter REST 단위 테스트 (httpx.MockTransport 기반).

6건: 국내 place / 해외 place / cancel / get_positions / get_balance / health_check
respx 미설치 환경에서 httpx.MockTransport 로 대체.
"""
from __future__ import annotations

import json
import pytest
import httpx
from decimal import Decimal
from datetime import datetime, timezone

from src.brokers.base import OrderRequest, OrderType, PositionSide, HealthStatus
from src.brokers.errors import BrokerClosedError, UnsupportedOperationError
from src.brokers.kis.async_adapter import KISAsyncAdapter
from src.brokers.kis.async_http import KISAsyncClient
from src.brokers.kis.async_ws import KISAsyncWebSocket
from src.brokers.kis.auth import KISAuth
from src.execution.base import Side, TimeInForce


# ---------------------------------------------------------------------------
# MockTransport helpers
# ---------------------------------------------------------------------------

class _RoutingTransport(httpx.AsyncBaseTransport):
    """path prefix → json response 라우팅 mock transport."""

    def __init__(self, routes: dict[str, dict]) -> None:
        self._routes = routes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for prefix, body in self._routes.items():
            if path.startswith(prefix):
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": f"no route for {path}"})


def _make_client(routes: dict[str, dict], auth: KISAuth) -> KISAsyncClient:
    transport = _RoutingTransport(routes)
    http = httpx.AsyncClient(
        base_url="https://openapivts.koreainvestment.com:29443",
        transport=transport,
        trust_env=False,
    )
    client = KISAsyncClient(
        auth=auth,
        app_key="test_key",
        app_secret="test_secret",
        cano="12345678",
        acnt_prdt_cd="01",
        paper=True,
        http_client=http,
    )
    client._owns_http = False
    return client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_auth(tmp_path):
    auth = KISAuth(
        app_key="test_key",
        app_secret="test_secret",
        paper=True,
        cache_path=str(tmp_path / "tok.json"),
    )
    auth._access_token = "fake_token"
    auth._expires_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
    return auth


def _make_adapter(mock_auth: KISAuth, routes: dict[str, dict]) -> KISAsyncAdapter:
    adapter = KISAsyncAdapter.__new__(KISAsyncAdapter)
    adapter.paper = True
    adapter.name = "kis"
    adapter._kill_switch = None
    adapter._hts_id = "TESTUSR"
    adapter._closing = False
    adapter._auth = mock_auth
    adapter._client = _make_client(routes, mock_auth)

    ws = KISAsyncWebSocket.__new__(KISAsyncWebSocket)
    ws._closing = False
    ws._auth = mock_auth
    ws._app_key = "test_key"
    ws._hts_id = "TESTUSR"
    ws._paper = True
    ws._tr_ids = {"ws_execution": "H0STCNI9"}
    ws._tr_id = "H0STCNI9"
    ws._aes_key = None
    ws._aes_iv = None
    ws._owns_http = False
    ws._http = httpx.AsyncClient(trust_env=False)
    adapter._ws = ws

    return adapter


def _order_req(side: Side = Side.BUY, symbol: str = "005930") -> OrderRequest:
    return OrderRequest(
        client_order_id="cli-001",
        symbol=symbol,
        side=side,
        qty=Decimal("10"),
        order_type=OrderType.LIMIT,
        price=Decimal("70000"),
        tif=TimeInForce.GTC,
    )


_ORDER_RESP = {
    "rt_cd": "0",
    "msg_cd": "",
    "msg1": "정상처리",
    "output": {"ODNO": "ORD-001", "ORD_TMD": "100000"},
}

_BALANCE_RESP = {
    "rt_cd": "0",
    "msg_cd": "",
    "msg1": "",
    "output1": [
        {
            "PDNO": "005930",
            "PRDT_NAME": "삼성전자",
            "HLDG_QTY": "10",
            "PCHS_AVG_PRIC": "70000",
            "EVLU_AMT": "700000",
        }
    ],
    "output2": [{"DNCA_TOT_AMT": "1000000"}],
}


# ---------------------------------------------------------------------------
# Test 1: 국내 place_order (BUY)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_place_order_domestic_buy(mock_auth):
    adapter = _make_adapter(mock_auth, {
        "/uapi/domestic-stock/v1/trading/order-cash": _ORDER_RESP,
    })
    ack = await adapter.place_order(_order_req(Side.BUY))
    assert ack.broker_order_id == "ORD-001"
    assert ack.symbol == "005930"
    assert ack.status == "NEW"
    assert ack.qty == Decimal("10")


# ---------------------------------------------------------------------------
# Test 2: 해외(다른 종목) place_order (SELL)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_place_order_sell(mock_auth):
    resp = dict(_ORDER_RESP)
    resp["output"] = {"ODNO": "ORD-002", "ORD_TMD": "100001"}
    adapter = _make_adapter(mock_auth, {
        "/uapi/domestic-stock/v1/trading/order-cash": resp,
    })
    ack = await adapter.place_order(_order_req(Side.SELL, symbol="035720"))
    assert ack.broker_order_id == "ORD-002"
    assert ack.symbol == "035720"


# ---------------------------------------------------------------------------
# Test 3: cancel_order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_order(mock_auth):
    adapter = _make_adapter(mock_auth, {
        "/uapi/domestic-stock/v1/trading/order-cash": _ORDER_RESP,
    })
    await adapter.cancel_order(broker_order_id="ORD-001", symbol="005930")


@pytest.mark.asyncio
async def test_cancel_order_no_broker_id_raises(mock_auth):
    adapter = _make_adapter(mock_auth, {})
    with pytest.raises(UnsupportedOperationError):
        await adapter.cancel_order(symbol="005930")


# ---------------------------------------------------------------------------
# Test 4: get_positions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_positions(mock_auth):
    adapter = _make_adapter(mock_auth, {
        "/uapi/domestic-stock/v1/trading/inquire-balance": _BALANCE_RESP,
    })
    positions = await adapter.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "005930"
    assert positions[0].qty == Decimal("10")
    assert positions[0].entry_price == Decimal("70000")


@pytest.mark.asyncio
async def test_get_positions_filtered_by_symbol(mock_auth):
    adapter = _make_adapter(mock_auth, {
        "/uapi/domestic-stock/v1/trading/inquire-balance": _BALANCE_RESP,
    })
    positions = await adapter.get_positions(symbol="999999")
    assert positions == []


# ---------------------------------------------------------------------------
# Test 5: get_balance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_balance(mock_auth):
    adapter = _make_adapter(mock_auth, {
        "/uapi/domestic-stock/v1/trading/inquire-balance": _BALANCE_RESP,
    })
    balances = await adapter.get_balance()
    assert len(balances) == 1
    assert balances[0].asset == "KRW"
    assert balances[0].free == Decimal("1000000")
    assert balances[0].locked == Decimal("0")


# ---------------------------------------------------------------------------
# Test 6: health_check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_check_ok(mock_auth):
    adapter = _make_adapter(mock_auth, {})
    status = await adapter.health_check()
    assert status == HealthStatus.OK


@pytest.mark.asyncio
async def test_health_check_down(mock_auth):
    mock_auth._access_token = None
    mock_auth._expires_at = None
    adapter = _make_adapter(mock_auth, {})
    status = await adapter.health_check()
    assert status == HealthStatus.DOWN


# ---------------------------------------------------------------------------
# Test 7: place_order 시 closing=True → BrokerClosedError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_place_order_when_closing_raises(mock_auth):
    adapter = _make_adapter(mock_auth, {})
    adapter._closing = True
    with pytest.raises(BrokerClosedError):
        await adapter.place_order(_order_req())


# ---------------------------------------------------------------------------
# Test 8: reduce_only + BUY → UnsupportedOperationError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reduce_only_buy_raises(mock_auth):
    adapter = _make_adapter(mock_auth, {})
    req = _order_req(Side.BUY)
    req.reduce_only = True
    with pytest.raises(UnsupportedOperationError):
        await adapter.place_order(req)


# ---------------------------------------------------------------------------
# Test 9: aclose idempotent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aclose_idempotent(mock_auth):
    adapter = _make_adapter(mock_auth, {})
    await adapter.aclose()
    await adapter.aclose()  # 두 번 호출해도 예외 없음
    assert adapter._closing is True
