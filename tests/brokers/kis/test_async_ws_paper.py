"""KISAsyncWebSocket paper=True 단위테스트 (회귀).

5건: paper WS URL, AES key/iv 수신 후 enc_flag=1 복호화, exec_flag!=2 drop,
     _backoff_delay 지수+jitter 한계, reconnect counter.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.brokers.kis.async_ws import (
    KISAsyncWebSocket,
    _PAPER_WS_URL,
    _RECONNECT_BASE_DELAY,
    _RECONNECT_MAX_DELAY,
    _RECONNECT_JITTER,
)
from src.brokers.kis.auth import KISAuth


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_auth():
    auth = KISAuth.__new__(KISAuth)
    auth._app_key = "test_key"
    auth._app_secret = "test_secret"
    auth._paper = True
    auth._access_token = "fake_token"
    auth._expires_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
    auth._last_issued_at = 0.0
    auth._async_lock = None
    auth._cache_path = None
    return auth


@pytest.fixture
def ws_client(mock_auth):
    import httpx
    http = httpx.AsyncClient(trust_env=False)
    client = KISAsyncWebSocket(
        auth=mock_auth,
        app_key="test_key",
        hts_id="TESTUSR",
        paper=True,
        http_client=http,
    )
    client._owns_http = False
    return client


def _make_execution_payload(exec_flag: str = "2", order_no: str = "ORD-001") -> str:
    fields = [""] * 23
    fields[2] = order_no
    fields[8] = "005930"   # symbol
    fields[9] = "10"       # qty
    fields[10] = "70000"   # price
    fields[11] = "103000"  # ts_str
    fields[13] = exec_flag
    return "^".join(fields)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_paper_ws_url_used(ws_client):
    """paper=True 시 _PAPER_WS_URL 사용."""
    assert ws_client._ws_url == _PAPER_WS_URL
    assert "31000" in ws_client._ws_url


def test_aes_key_stored_on_subscription_ack(ws_client):
    """구독 응답에 AES key/iv 포함 시 저장."""
    ack_msg = json.dumps({
        "header": {"tr_id": ws_client._tr_id},
        "body": {
            "rt_cd": "0",
            "output": {"key": "test-aes-key-32b", "iv": "test-iv-16bytes!"},
        },
    })
    result = ws_client._handle_message(ack_msg)
    assert result is None  # subscription ack yields no fill
    assert ws_client._aes_key == "test-aes-key-32b"
    assert ws_client._aes_iv == "test-iv-16bytes!"


def test_enc_flag_1_decrypts_payload(ws_client):
    """enc_flag=1 시 AES 복호화 후 파싱."""
    plain_payload = _make_execution_payload(exec_flag="2", order_no="ORD-ENC")
    ws_client._aes_key = "fake-key"
    ws_client._aes_iv = "fake-iv"

    with patch(
        "src.brokers.kis.async_ws.decrypt_aes256_cbc_pkcs7",
        return_value=plain_payload,
    ) as mock_decrypt:
        message = f"1|{ws_client._tr_id}|1|encrypted-blob"
        fill = ws_client._handle_message(message)

    mock_decrypt.assert_called_once_with("encrypted-blob", "fake-key", "fake-iv")
    assert fill is not None
    assert fill.broker_order_id == "ORD-ENC"
    assert fill.qty == Decimal("10")
    assert fill.price == Decimal("70000")


def test_exec_flag_not_2_returns_none(ws_client):
    """exec_flag != '2' (부분체결 등) → None 반환 (drop)."""
    for flag in ("0", "1", "3", "9"):
        payload = _make_execution_payload(exec_flag=flag)
        message = f"0|{ws_client._tr_id}|1|{payload}"
        result = ws_client._handle_message(message)
        assert result is None, f"Expected None for exec_flag={flag}"


def test_backoff_delay_bounds():
    """_backoff_delay: base 1s, max 10s, jitter 비율 ≤ RECONNECT_JITTER."""
    import httpx
    from src.brokers.kis.auth import KISAuth

    auth = KISAuth.__new__(KISAuth)
    auth._app_secret = "s"
    ws = KISAsyncWebSocket.__new__(KISAsyncWebSocket)

    # attempt=0: delay ~ base (1s), bounded [0, 1*(1+jitter)]
    for _ in range(50):
        d = ws._backoff_delay(0)
        assert 0.0 <= d <= _RECONNECT_BASE_DELAY * (1 + _RECONNECT_JITTER) + 0.01

    # attempt=10: delay ~ max (10s) ± jitter
    for _ in range(50):
        d = ws._backoff_delay(10)
        assert d <= _RECONNECT_MAX_DELAY * (1 + _RECONNECT_JITTER) + 0.01

    # delay never exceeds max*(1+jitter)
    for attempt in range(20):
        d = ws._backoff_delay(attempt)
        assert d >= 0.0
        assert d <= _RECONNECT_MAX_DELAY * (1 + _RECONNECT_JITTER) + 0.01


@pytest.mark.asyncio
async def test_reconnect_increments_attempt(ws_client):
    """ConnectionClosed 시 attempt 증가 + backoff 후 재접속."""
    import websockets.exceptions

    approval_call_count = 0

    async def fake_approval():
        nonlocal approval_call_count
        approval_call_count += 1
        if approval_call_count >= 2:
            ws_client._closing = True
        return "fake-approval-key"

    ws_client._get_approval_key = fake_approval

    with patch("websockets.connect") as mock_connect:
        # First connection raises ConnectionClosed; second call sets closing=True
        mock_ws = AsyncMock()
        mock_ws.__aenter__ = AsyncMock(
            side_effect=websockets.exceptions.ConnectionClosed(None, None)
        )
        mock_ws.__aexit__ = AsyncMock(return_value=False)
        mock_connect.return_value = mock_ws

        with patch.object(ws_client, "_backoff_delay", return_value=0.0):
            fills = []
            async for fill in ws_client.stream_fills():
                fills.append(fill)

    # approval_key fetched twice (initial + reconnect attempt)
    assert approval_call_count == 2
