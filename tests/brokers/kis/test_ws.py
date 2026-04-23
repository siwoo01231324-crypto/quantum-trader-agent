from __future__ import annotations

import base64
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.brokers.kis.crypto import decrypt_aes256_cbc_pkcs7
from src.brokers.kis.tr_ids import tr_ids_for
from src.brokers.kis.ws import (
    KISWebSocket,
    _LIVE_WS_URL,
    _PAPER_WS_URL,
    _MAX_SUBSCRIPTIONS,
)


# ---------------------------------------------------------------------------
# Helpers: generate real AES-256-CBC test vectors
# ---------------------------------------------------------------------------

def _make_aes_fixture(plaintext: str) -> tuple[str, str, str]:
    """AES-256-CBC + PKCS7 로 평문 암호화, (ciphertext_b64, key_b64, iv_b64) 반환."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    import os

    key = os.urandom(32)  # 256-bit
    iv = os.urandom(16)   # 128-bit block

    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    enc = cipher.encryptor()
    ciphertext = enc.update(padded) + enc.finalize()

    return (
        base64.b64encode(ciphertext).decode(),
        base64.b64encode(key).decode(),
        base64.b64encode(iv).decode(),
    )


def _make_execution_payload(
    order_no: str = "0000001",
    symbol: str = "005930",
    qty: str = "10",
    price: str = "70000",
    exec_flag: str = "2",  # 2=체결
    ts: str = "091500",
) -> str:
    """^ 구분자 23 필드 체결통보 페이로드 생성."""
    fields = [
        "user123",     # 0: 고객ID
        "12345678-01", # 1: 계좌번호
        order_no,      # 2: 주문번호
        "0000000",     # 3: 원주문번호
        "02",          # 4: 매도매수구분 (02=매수)
        "0",           # 5: 정정구분
        "00",          # 6: 주문종류
        "0",           # 7: 주문조건
        symbol,        # 8: 종목코드
        qty,           # 9: 체결수량
        price,         # 10: 체결단가
        ts,            # 11: 체결시각 HHMMSS
        "0",           # 12: 거부여부
        exec_flag,     # 13: 체결여부
        "1",           # 14: 접수여부
        "0",           # 15: 지점번호
        qty,           # 16: 주문수량
        "홍길동",      # 17: 계좌명
        "삼성전자",    # 18: 체결종목명
        "00",          # 19: 신용구분
        "",            # 20: 신용대출일자
        "삼성전자",    # 21: 체결종목명40
        price,         # 22: 주문단가
    ]
    return "^".join(fields)


# ---------------------------------------------------------------------------
# AES-256-CBC + PKCS7 복호화 테스트
# ---------------------------------------------------------------------------

class TestDecryptAES256CBCPKCS7:
    def test_decrypt_roundtrip(self):
        plaintext = "hello KIS WS payload"
        ct_b64, key_b64, iv_b64 = _make_aes_fixture(plaintext)
        result = decrypt_aes256_cbc_pkcs7(ct_b64, key_b64, iv_b64)
        assert result == plaintext

    def test_decrypt_23_field_payload(self):
        payload = _make_execution_payload()
        ct_b64, key_b64, iv_b64 = _make_aes_fixture(payload)
        result = decrypt_aes256_cbc_pkcs7(ct_b64, key_b64, iv_b64)
        assert result == payload

    def test_decrypt_korean_text(self):
        plaintext = "삼성전자^10^70000"
        ct_b64, key_b64, iv_b64 = _make_aes_fixture(plaintext)
        result = decrypt_aes256_cbc_pkcs7(ct_b64, key_b64, iv_b64)
        assert result == plaintext

    def test_wrong_key_raises(self):
        ct_b64, key_b64, iv_b64 = _make_aes_fixture("test")
        import os
        bad_key = base64.b64encode(os.urandom(32)).decode()
        with pytest.raises(Exception):
            decrypt_aes256_cbc_pkcs7(ct_b64, bad_key, iv_b64)


# ---------------------------------------------------------------------------
# 구독 응답 AES key/iv 추출
# ---------------------------------------------------------------------------

class TestSubscribeReturnsAESKeyIV:
    def _make_ws(self, paper=True):
        auth = MagicMock()
        auth._app_secret = "fake-secret"
        auth.get_token.return_value = "fake-token"
        return KISWebSocket(
            auth=auth,
            app_key="fake-key",
            hts_id="user123",
            paper=paper,
        )

    def test_subscription_response_extracts_key_iv(self):
        ws = self._make_ws()
        sub_response = json.dumps({
            "header": {"tr_id": "H0STCNI9"},
            "body": {
                "rt_cd": "0",
                "output": {
                    "key": "dGVzdC1rZXktMzItYnl0ZXMtZm9yLWFlczI1NmNiYw==",
                    "iv": "dGVzdC1pdi0xNmI=",
                }
            }
        })
        ws._handle_message(sub_response)
        assert ws._aes_key == "dGVzdC1rZXktMzItYnl0ZXMtZm9yLWFlczI1NmNiYw=="
        assert ws._aes_iv == "dGVzdC1pdi0xNmI="
        assert ws._execution_subscribed is True

    def test_subscription_failure_no_key_iv(self):
        ws = self._make_ws()
        sub_response = json.dumps({
            "header": {"tr_id": "H0STCNI9"},
            "body": {"rt_cd": "1", "output": {}}
        })
        ws._handle_message(sub_response)
        assert ws._aes_key is None
        assert ws._aes_iv is None


# ---------------------------------------------------------------------------
# ^ 구분자 23 필드 파싱
# ---------------------------------------------------------------------------

class TestParsesCaretDelimited23Fields:
    def _make_ws(self, paper=True):
        auth = MagicMock()
        auth._app_secret = "fake-secret"
        ws = KISWebSocket(auth=auth, app_key="k", hts_id="uid", paper=paper)
        return ws

    def test_parse_execution_fill(self):
        ws = self._make_ws()
        payload = _make_execution_payload(order_no="1234567", qty="10", price="70000")
        fill = ws._parse_execution(payload)
        assert fill is not None
        assert fill.broker_order_id == "1234567"
        assert fill.qty == Decimal("10")
        assert fill.price == Decimal("70000")
        assert fill.fee_asset == "KRW"

    def test_non_execution_event_returns_none(self):
        ws = self._make_ws()
        payload = _make_execution_payload(exec_flag="1")  # 1=접수
        fill = ws._parse_execution(payload)
        assert fill is None

    def test_rejection_event_returns_none(self):
        ws = self._make_ws()
        payload = _make_execution_payload(exec_flag="0")  # 0=거부
        fill = ws._parse_execution(payload)
        assert fill is None

    def test_insufficient_fields_returns_none(self):
        ws = self._make_ws()
        fill = ws._parse_execution("only^a^few^fields")
        assert fill is None

    def test_encrypted_message_dispatches_fill(self):
        fills = []
        auth = MagicMock()
        auth._app_secret = "s"
        ws = KISWebSocket(auth=auth, app_key="k", hts_id="uid", paper=True, on_fill=fills.append)

        payload = _make_execution_payload()
        ct_b64, key_b64, iv_b64 = _make_aes_fixture(payload)
        ws._aes_key = key_b64
        ws._aes_iv = iv_b64

        message = f"1|H0STCNI9|1|{ct_b64}"
        ws._handle_message(message)
        assert len(fills) == 1
        assert fills[0].qty == Decimal("10")

    def test_unencrypted_message_dispatches_fill(self):
        fills = []
        auth = MagicMock()
        auth._app_secret = "s"
        ws = KISWebSocket(auth=auth, app_key="k", hts_id="uid", paper=True, on_fill=fills.append)

        payload = _make_execution_payload()
        message = f"0|H0STCNI9|1|{payload}"
        ws._handle_message(message)
        assert len(fills) == 1


# ---------------------------------------------------------------------------
# HTS ID as tr_key (종목코드 아님)
# ---------------------------------------------------------------------------

class TestHTSIdAsTrKey:
    def test_hts_id_used_as_tr_key_not_symbol(self):
        auth = MagicMock()
        auth._app_secret = "secret"
        ws = KISWebSocket(auth=auth, app_key="key", hts_id="my_hts_user_id", paper=True)

        with patch.object(ws, "_get_approval_key", return_value="approval-key-123"):
            sent_messages = []
            mock_ws = MagicMock()
            mock_ws.send = lambda msg: sent_messages.append(msg)
            ws._on_open(mock_ws)

        assert len(sent_messages) == 1
        msg = json.loads(sent_messages[0])
        assert msg["body"]["input"]["tr_key"] == "my_hts_user_id"


# ---------------------------------------------------------------------------
# paper vs live URL + TR_ID
# ---------------------------------------------------------------------------

class TestPaperVsLiveUrlAndTrId:
    def test_paper_url(self):
        auth = MagicMock()
        auth._app_secret = "s"
        ws = KISWebSocket(auth=auth, app_key="k", hts_id="u", paper=True)
        assert ws._ws_url == _PAPER_WS_URL
        assert ws._ws_url == "ws://ops.koreainvestment.com:31000"

    def test_live_url(self):
        auth = MagicMock()
        auth._app_secret = "s"
        ws = KISWebSocket(auth=auth, app_key="k", hts_id="u", paper=False)
        assert ws._ws_url == _LIVE_WS_URL
        assert ws._ws_url == "ws://ops.koreainvestment.com:21000"

    def test_paper_tr_id(self):
        auth = MagicMock()
        auth._app_secret = "s"
        ws = KISWebSocket(auth=auth, app_key="k", hts_id="u", paper=True)
        assert ws._tr_id == "H0STCNI9"

    def test_live_tr_id(self):
        auth = MagicMock()
        auth._app_secret = "s"
        ws = KISWebSocket(auth=auth, app_key="k", hts_id="u", paper=False)
        assert ws._tr_id == "H0STCNI0"


# ---------------------------------------------------------------------------
# 세션 한도 경고
# ---------------------------------------------------------------------------

class TestSessionLimitWarning:
    def test_subscription_count_beyond_limit_logs_warning(self, caplog):
        import logging
        auth = MagicMock()
        auth._app_secret = "s"
        ws = KISWebSocket(auth=auth, app_key="k", hts_id="u", paper=True)
        ws._subscription_count = _MAX_SUBSCRIPTIONS  # already at max

        with patch.object(ws, "_get_approval_key", return_value="ak"):
            mock_ws_app = MagicMock()
            with caplog.at_level(logging.WARNING, logger="src.brokers.kis.ws"):
                ws._on_open(mock_ws_app)

        assert any("한도 초과" in r.message or "한도" in r.message for r in caplog.records)

    def test_second_execution_subscription_blocked(self, caplog):
        import logging
        auth = MagicMock()
        auth._app_secret = "s"
        ws = KISWebSocket(auth=auth, app_key="k", hts_id="u", paper=True)
        ws._execution_subscribed = True  # already subscribed

        mock_ws_app = MagicMock()
        with caplog.at_level(logging.WARNING, logger="src.brokers.kis.ws"):
            ws._on_open(mock_ws_app)

        # Should not send any subscribe message
        mock_ws_app.send.assert_not_called()
