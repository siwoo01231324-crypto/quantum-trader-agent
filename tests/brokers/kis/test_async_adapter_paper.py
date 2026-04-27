"""KISAsyncAdapter paper=True 단위테스트 (회귀).

5건: base_url 분기, credit_number 파싱, place_order NEW ack, get_balance KRW, aclose idempotent.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from src.brokers.base import OrderRequest, OrderType, PositionSide
from src.brokers.kis.async_adapter import KISAsyncAdapter, _parse_credit_number
from src.brokers.kis.async_http import KISAsyncClient
from src.brokers.kis.async_ws import KISAsyncWebSocket
from src.brokers.kis.auth import KISAuth
from src.execution.base import Side, TimeInForce


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RoutingTransport(httpx.AsyncBaseTransport):
    def __init__(self, routes: dict[str, dict]) -> None:
        self._routes = routes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for prefix, body in self._routes.items():
            if path.startswith(prefix):
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": f"no route for {path}"})


def _make_adapter(auth: KISAuth, routes: dict[str, dict]) -> KISAsyncAdapter:
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

    adapter = KISAsyncAdapter.__new__(KISAsyncAdapter)
    adapter.paper = True
    adapter._kill_switch = None
    adapter._closing = False
    adapter._auth = auth
    adapter._client = client

    ws = KISAsyncWebSocket.__new__(KISAsyncWebSocket)
    ws._closing = False
    adapter._ws = ws
    return adapter


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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_paper_true_uses_paper_base_url(tmp_path):
    """KISAuth(paper=True) 시 base_url = openapivts."""
    auth = KISAuth(
        app_key="k", app_secret="s", paper=True, cache_path=str(tmp_path / "t.json")
    )
    assert "openapivts" in auth._base_url


def test_parse_credit_number_valid():
    """_parse_credit_number 정상 포맷."""
    cano, acnt = _parse_credit_number("12345678-01")
    assert cano == "12345678"
    assert acnt == "01"


def test_parse_credit_number_invalid():
    """_parse_credit_number 잘못된 포맷 → ConfigurationError."""
    from src.brokers.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        _parse_credit_number("INVALID")


@pytest.mark.asyncio
async def test_place_order_returns_new_status(mock_auth):
    """place_order → status='NEW' OrderAck."""
    routes = {
        "/uapi/domestic-stock/v1/trading/order-cash": {
            "rt_cd": "0",
            "msg_cd": "APBK0013",
            "msg1": "주문 전송 완료",
            "output": {"ODNO": "0000123456", "ORD_TMD": "103000", "KRX_FWDG_ORD_ORGNO": "", "ODNO": "0000123456"},
        }
    }
    adapter = _make_adapter(mock_auth, routes)
    req = OrderRequest(
        symbol="005930",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        qty=Decimal("10"),
        price=Decimal("70000"),
        client_order_id="test-001",
        tif=TimeInForce.GTC,
    )
    ack = await adapter.place_order(req)
    assert ack.status == "NEW"
    assert ack.broker_order_id == "0000123456"
    assert ack.symbol == "005930"


@pytest.mark.asyncio
async def test_get_balance_returns_krw(mock_auth):
    """get_balance → KRW Balance."""
    routes = {
        "/uapi/domestic-stock/v1/trading/inquire-balance": {
            "rt_cd": "0",
            "msg_cd": "20200301",
            "msg1": "조회되었습니다.",
            "output1": [],
            "output2": [{"DNCA_TOT_AMT": "1000000"}],
        }
    }
    adapter = _make_adapter(mock_auth, routes)
    balances = await adapter.get_balance()
    assert len(balances) == 1
    assert balances[0].asset == "KRW"
    assert balances[0].free == Decimal("1000000")


@pytest.mark.asyncio
async def test_aclose_idempotent(mock_auth):
    """aclose() 두 번 호출해도 예외 없음 (idempotent)."""
    from unittest.mock import AsyncMock

    adapter = _make_adapter(mock_auth, {})
    adapter._ws.aclose = AsyncMock()
    adapter._client.aclose = AsyncMock()

    await adapter.aclose()
    await adapter.aclose()  # second call — no-op

    # WS aclose called once (second call returns early)
    adapter._ws.aclose.assert_called_once()
