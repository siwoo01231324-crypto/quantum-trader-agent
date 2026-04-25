"""KIS async WebSocket 단위 테스트.

2건: 체결통보 파싱 / reconnect + 구독 복원
"""
from __future__ import annotations

import asyncio
import json
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from src.brokers.kis.async_ws import KISAsyncWebSocket
from src.brokers.kis.auth import KISAuth
from src.brokers.types import BrokerFill


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_auth(tmp_path):
    auth = KISAuth.__new__(KISAuth)
    auth._app_key = "test_key"
    auth._app_secret = "test_secret"
    auth._paper = True
    auth._access_token = "fake_token"
    from datetime import datetime, timezone
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


# ---------------------------------------------------------------------------
# Test 1: 체결통보 메시지 파싱
# ---------------------------------------------------------------------------

def test_parse_execution_encrypted_fill(ws_client):
    """AES 복호화 없이 비암호화(enc_flag=0) 체결 메시지 파싱 검증."""
    # 23필드 ^ 구분 페이로드 구성 (fields[13]="2" = 체결)
    fields = [""] * 23
    fields[2] = "ORD-123"   # 주문번호
    fields[8] = "005930"    # 종목코드
    fields[9] = "5"         # 체결수량
    fields[10] = "70000"    # 체결단가
    fields[11] = "103000"   # 체결시각 HHMMSS
    fields[13] = "2"        # 체결여부=체결
    payload = "^".join(fields)

    # 비암호화 데이터 메시지 형식: "0|TR_ID|count|payload"
    message = f"0|{ws_client._tr_id}|1|{payload}"

    fill = ws_client._handle_message(message)
    assert fill is not None
    assert fill.broker_order_id == "ORD-123"
    assert fill.qty == Decimal("5")
    assert fill.price == Decimal("70000")
    assert fill.trade_id == "ORD-123:103000"


def test_parse_execution_non_fill_ignored(ws_client):
    """체결여부=접수(1)인 메시지는 None 반환."""
    fields = [""] * 23
    fields[13] = "1"  # 접수
    payload = "^".join(fields)
    message = f"0|{ws_client._tr_id}|1|{payload}"
    fill = ws_client._handle_message(message)
    assert fill is None


def test_subscription_response_stores_aes_keys(ws_client):
    """구독 응답 JSON 처리 시 AES key/iv 저장."""
    msg = json.dumps({
        "header": {"tr_id": ws_client._tr_id},
        "body": {
            "rt_cd": "0",
            "output": {"key": "base64key==", "iv": "base64iv=="},
        },
    })
    result = ws_client._handle_message(msg)
    assert result is None
    assert ws_client._aes_key == "base64key=="
    assert ws_client._aes_iv == "base64iv=="


def test_encrypted_message_without_keys_returns_none(ws_client):
    """AES key/iv 없는 상태에서 암호화 메시지 → None 반환 (경고 로그만)."""
    ws_client._aes_key = None
    ws_client._aes_iv = None
    message = f"1|{ws_client._tr_id}|1|someciphertext"
    fill = ws_client._handle_message(message)
    assert fill is None


# ---------------------------------------------------------------------------
# Test 2: reconnect + 구독 복원 (stream_fills generator)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_fills_reconnects_on_disconnect(ws_client):
    """WS 연결이 끊기면 재접속 후 구독 재수행."""
    import websockets.exceptions

    fields = [""] * 23
    fields[2] = "ORD-999"
    fields[8] = "005930"
    fields[9] = "3"
    fields[10] = "68000"
    fields[11] = "110000"
    fields[13] = "2"
    payload = "^".join(fields)
    fill_msg = f"0|{ws_client._tr_id}|1|{payload}"

    call_count = 0

    class FakeWS:
        async def send(self, msg):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise websockets.exceptions.ConnectionClosedOK(None, None)
            elif call_count == 2:
                return fill_msg
            else:
                raise StopAsyncIteration

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    approval_call_count = 0

    async def fake_get_approval_key():
        nonlocal approval_call_count
        approval_call_count += 1
        return "approval_key_test"

    ws_client._get_approval_key = fake_get_approval_key

    with patch("websockets.connect", return_value=FakeWS()):
        fills = []
        gen = ws_client.stream_fills()
        try:
            async with asyncio.timeout(5.0):
                async for fill in gen:
                    fills.append(fill)
                    ws_client._closing = True
                    break
        except (asyncio.TimeoutError, StopAsyncIteration):
            pass

    assert len(fills) == 1
    assert fills[0].broker_order_id == "ORD-999"
    # approval_key 가 두 번 발급됨 (최초 연결 실패 후 재접속)
    assert approval_call_count >= 1


@pytest.mark.asyncio
async def test_aclose_sets_closing_flag(ws_client):
    """aclose() 호출 시 _closing=True 로 스트림 종료 신호."""
    assert not ws_client._closing
    await ws_client.aclose()
    assert ws_client._closing
