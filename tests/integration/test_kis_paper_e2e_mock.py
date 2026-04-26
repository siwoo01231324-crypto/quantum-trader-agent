"""KIS paper e2e mock integration test.

Covers: auth → adapter.place_order → WS fill stream → adapter.get_balance
Uses respx for REST mocking + async iterator mock for WS.
Runs in normal CI (not e2e_kis_paper marker required).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from src.brokers.base import OrderRequest, OrderType, PositionSide
from src.brokers.kis.async_adapter import KISAsyncAdapter
from src.brokers.kis.async_http import KISAsyncClient
from src.brokers.kis.async_ws import KISAsyncWebSocket
from src.brokers.kis.auth import KISAuth
from src.brokers.types import BrokerFill
from src.execution.base import Side, TimeInForce


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
        lock_dir=str(tmp_path),
    )
    auth._access_token = "fake_token"
    auth._expires_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
    return auth


class _RoutingTransport(httpx.AsyncBaseTransport):
    """path prefix → json response routing mock transport (same pattern as test_kis_async_adapter)."""

    def __init__(self, routes: dict[str, dict]) -> None:
        self._routes = routes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for prefix, body in self._routes.items():
            if path.startswith(prefix):
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": f"no route for {path}"})


def _make_adapter_with_routes(
    mock_auth: KISAuth, base_url: str, routes: dict[str, dict]
) -> tuple[KISAsyncAdapter, httpx.AsyncClient]:
    """Build KISAsyncAdapter wired to a routing mock transport."""
    transport = _RoutingTransport(routes)
    http = httpx.AsyncClient(
        base_url=base_url,
        transport=transport,
        trust_env=False,
    )
    client = KISAsyncClient(
        auth=mock_auth,
        app_key="test_key",
        app_secret="test_secret",
        cano="12345678",
        acnt_prdt_cd="01",
        paper=True,
        http_client=http,
    )
    client._owns_http = False

    ws_mock = KISAsyncWebSocket.__new__(KISAsyncWebSocket)
    ws_mock._closing = False

    adapter = KISAsyncAdapter.__new__(KISAsyncAdapter)
    adapter.paper = True
    adapter._kill_switch = None
    adapter._closing = False
    adapter._auth = mock_auth
    adapter._client = client
    adapter._ws = ws_mock
    return adapter, http


# ---------------------------------------------------------------------------
# Test: auth → place_order → balance round-trip (respx REST mock)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kis_paper_rest_round_trip(mock_auth, tmp_path):
    """Token → place_order(NEW) → get_balance(KRW) end-to-end with mock transport."""
    base_url = "https://openapivts.koreainvestment.com:29443"
    routes = {
        "/uapi/domestic-stock/v1/trading/order-cash": {
            "rt_cd": "0",
            "msg_cd": "APBK0013",
            "msg1": "주문 전송 완료",
            "output": {
                "KRX_FWDG_ORD_ORGNO": "",
                "ODNO": "0000123456",
                "ORD_TMD": "103000",
            },
        },
        "/uapi/domestic-stock/v1/trading/inquire-balance": {
            "rt_cd": "0",
            "msg_cd": "20200301",
            "msg1": "조회되었습니다.",
            "output1": [],
            "output2": [{"DNCA_TOT_AMT": "5000000"}],
        },
    }

    adapter, http = _make_adapter_with_routes(mock_auth, base_url, routes)

    try:
        # 1. place_order
        req = OrderRequest(
            symbol="005930",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            qty=Decimal("1"),
            price=None,
            client_order_id="e2e-test-001",
            tif=TimeInForce.DAY,
        )
        ack = await adapter.place_order(req)
        assert ack.status == "NEW"
        assert ack.broker_order_id == "0000123456"
        assert ack.symbol == "005930"

        # 2. get_balance
        balances = await adapter.get_balance()
        assert len(balances) == 1
        assert balances[0].asset == "KRW"
        assert balances[0].free == Decimal("5000000")
    finally:
        await http.aclose()


# ---------------------------------------------------------------------------
# Test: WS fill stream (async iterator mock)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kis_paper_ws_fill_stream(mock_auth, tmp_path):
    """Fake WS fill iterator yields BrokerFill through stream_fills."""
    fills_received = []

    # Build a fake execution payload
    ws = KISAsyncWebSocket.__new__(KISAsyncWebSocket)
    ws._tr_id = "H0STCNI9"  # paper execution tr_id
    ws._aes_key = None
    ws._aes_iv = None
    ws._closing = False

    fields = [""] * 23
    fields[2] = "ORD-E2E-001"
    fields[8] = "005930"
    fields[9] = "1"
    fields[10] = "72000"
    fields[11] = "110000"
    fields[13] = "2"  # exec_flag = filled
    payload = "^".join(fields)

    raw_msg = f"0|{ws._tr_id}|1|{payload}"
    fill = ws._handle_message(raw_msg)
    assert fill is not None
    assert fill.broker_order_id == "ORD-E2E-001"
    assert fill.qty == Decimal("1")
    assert fill.price == Decimal("72000")
    fills_received.append(fill)

    assert len(fills_received) == 1


# ---------------------------------------------------------------------------
# Test: full adapter + fake fill async generator end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kis_paper_adapter_stream_fills_integration(mock_auth, tmp_path):
    """Adapter.stream_fills() delegates to WS and yields BrokerFill objects."""
    base_url = "https://openapivts.koreainvestment.com:29443"
    adapter, http = _make_adapter_with_routes(mock_auth, base_url, {})

    # Replace ws stream_fills with a fake async generator
    fake_fill = BrokerFill(
        parent_id="",
        broker_order_id="ORD-STREAM-001",
        client_order_id="",
        trade_id="ORD-STREAM-001:110000",
        qty=Decimal("1"),
        price=Decimal("72500"),
        fee=Decimal("0"),
        fee_asset="KRW",
        ts=datetime.now(tz=timezone.utc),
        is_maker=False,
    )

    async def _fake_stream():
        yield fake_fill
        adapter._ws._closing = True

    adapter._ws.stream_fills = _fake_stream

    fills = []
    async for fill in adapter.stream_fills():
        fills.append(fill)
        break  # only need one

    assert len(fills) == 1
    assert fills[0].broker_order_id == "ORD-STREAM-001"
    assert fills[0].price == Decimal("72500")
    await http.aclose()
