"""Unit tests for src.brokers.bitget.async_http signing + header building.

Verifies the HMAC-SHA256 + base64 envelope matches Bitget v2 docs format.
Reference: https://www.bitget.com/api-doc/common/signature
"""
from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from src.brokers.bitget.async_http import (
    AsyncBitgetFuturesClient,
    DEMO_PRODUCT_TYPE,
    LIVE_PRODUCT_TYPE,
)


@pytest.fixture
def client() -> AsyncBitgetFuturesClient:
    return AsyncBitgetFuturesClient(
        api_key="test-key",
        secret="test-secret",
        passphrase="test-pass",
        paper=True,
    )


def test_sign_matches_hmac_sha256_base64(client: AsyncBitgetFuturesClient):
    ts = "1700000000000"
    method = "GET"
    path = "/api/v2/mix/market/contracts?productType=USDT-FUTURES"
    body = ""
    expected = base64.b64encode(
        hmac.new(b"test-secret", f"{ts}{method}{path}{body}".encode(), hashlib.sha256).digest()
    ).decode()

    actual = client._sign(ts, method, path, body)
    assert actual == expected


def test_sign_post_body_included(client: AsyncBitgetFuturesClient):
    body = '{"symbol":"BTCUSDT","side":"buy"}'
    ts = "1700000000001"
    expected = base64.b64encode(
        hmac.new(
            b"test-secret",
            f"{ts}POST/api/v2/mix/order/place-order{body}".encode(),
            hashlib.sha256,
        ).digest()
    ).decode()
    actual = client._sign(ts, "POST", "/api/v2/mix/order/place-order", body)
    assert actual == expected


def test_sign_case_sensitive_method(client: AsyncBitgetFuturesClient):
    # Method must be uppercase per Bitget docs — our sign normalizes via .upper()
    a = client._sign("1700000000002", "get", "/api/v2/x", "")
    b = client._sign("1700000000002", "GET", "/api/v2/x", "")
    assert a == b


def test_headers_demo_adds_paptrading_flag(client: AsyncBitgetFuturesClient):
    h = client._headers("1700000000003", "sig")
    assert h["paptrading"] == "1"
    assert h["ACCESS-KEY"] == "test-key"
    assert h["ACCESS-SIGN"] == "sig"
    assert h["ACCESS-PASSPHRASE"] == "test-pass"


def test_headers_live_omits_paptrading_flag():
    live = AsyncBitgetFuturesClient(
        api_key="k", secret="s", passphrase="p", paper=False,
    )
    h = live._headers("1700000000004", "sig")
    assert "paptrading" not in h


def test_product_type_demo_vs_live():
    demo = AsyncBitgetFuturesClient(api_key="k", secret="s", passphrase="p", paper=True)
    live = AsyncBitgetFuturesClient(api_key="k", secret="s", passphrase="p", paper=False)
    # 2026-06-04 — Bitget Demo uses *same* productType=USDT-FUTURES as live.
    # Routing differentiator is the paptrading header, NOT the productType.
    # Empirically verified against $5,000 USDT Demo account.
    assert demo.product_type == LIVE_PRODUCT_TYPE
    assert demo.product_type == DEMO_PRODUCT_TYPE
    assert live.product_type == LIVE_PRODUCT_TYPE
