"""Retry path / error handling tests for KISAsyncClient.

Covers uncovered branches in src/brokers/kis/async_http.py:
- 5xx exponential backoff retry (succeeds after 2nd attempt)
- 5xx exhausted retries raises
- network error retry path (RequestError)
- network error exhausted
- rt_cd="1" business error raises
- aclose() on owned http client
- aclose() on injected http client (no-op)
- non-paper (real) base URL selection
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from src.brokers.errors import BrokerError
from src.brokers.kis.async_http import KISAsyncClient
from src.brokers.kis.auth import KISAuth


def _make_auth() -> KISAuth:
    auth = KISAuth(app_key="k", app_secret="s", paper=True)
    # Stub get_token_async to avoid real HTTP call.
    auth.get_token_async = AsyncMock(return_value="dummy-token")  # type: ignore[assignment]
    return auth


def _make_client(paper: bool = True, *, http_client=None) -> KISAsyncClient:
    return KISAsyncClient(
        auth=_make_auth(),
        app_key="k",
        app_secret="s",
        cano="12345678",
        acnt_prdt_cd="01",
        paper=paper,
        http_client=http_client,
    )


@pytest.mark.asyncio
async def test_base_url_paper_vs_real():
    paper = _make_client(paper=True)
    real = _make_client(paper=False)
    assert "openapivts" in paper._base_url
    assert "openapi.koreainvestment.com:9443" in real._base_url
    await paper.aclose()
    await real.aclose()


@pytest.mark.asyncio
async def test_rt_cd_business_error_raises():
    """When the API returns rt_cd='1', _check_response raises BrokerError."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={"rt_cd": "1", "msg_cd": "EGW00001", "msg1": "잘못된 요청"},
            )
        ),
        base_url="https://openapivts.koreainvestment.com:29443",
    ) as http:
        client = _make_client(http_client=http)
        with pytest.raises(BrokerError):
            await client.get_balance()


@pytest.mark.asyncio
async def test_5xx_retry_then_succeeds(monkeypatch):
    """5xx response → backoff → 2nd attempt succeeds."""
    # Speed up backoff: patch the internal delay constant
    monkeypatch.setattr("src.brokers.kis.async_http._RETRY_BASE_DELAY", 0.001)

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg_cd": "",
                "msg1": "",
                "output1": [],
                "output2": [{"DNCA_TOT_AMT": "1000"}],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openapivts.koreainvestment.com:29443",
    ) as http:
        client = _make_client(http_client=http)
        resp = await client.get_balance()
        assert call_count == 2  # 1 fail + 1 retry success
        assert resp is not None


@pytest.mark.asyncio
async def test_5xx_exhausted_raises(monkeypatch):
    """5xx persists for all 3 attempts → HTTPStatusError raised."""
    monkeypatch.setattr("src.brokers.kis.async_http._RETRY_BASE_DELAY", 0.001)

    handler = lambda r: httpx.Response(503, text="Service Unavailable")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openapivts.koreainvestment.com:29443",
    ) as http:
        client = _make_client(http_client=http)
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_balance()


@pytest.mark.asyncio
async def test_network_error_retry_then_succeeds(monkeypatch):
    """RequestError (network) → retry → success."""
    monkeypatch.setattr("src.brokers.kis.async_http._RETRY_BASE_DELAY", 0.001)

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("temporary dns failure")
        return httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "msg_cd": "",
                "msg1": "",
                "output1": [],
                "output2": [{"DNCA_TOT_AMT": "5000"}],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openapivts.koreainvestment.com:29443",
    ) as http:
        client = _make_client(http_client=http)
        resp = await client.get_balance()
        assert call_count == 2
        assert resp is not None


@pytest.mark.asyncio
async def test_network_error_exhausted_raises(monkeypatch):
    """RequestError persists → exhaust retries → raise."""
    monkeypatch.setattr("src.brokers.kis.async_http._RETRY_BASE_DELAY", 0.001)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("permanent failure")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openapivts.koreainvestment.com:29443",
    ) as http:
        client = _make_client(http_client=http)
        with pytest.raises(httpx.ConnectError):
            await client.get_balance()


@pytest.mark.asyncio
async def test_aclose_noop_when_http_injected():
    """When http_client is injected, aclose() does not close it (not owned)."""
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"rt_cd": "0"}))
    http = httpx.AsyncClient(transport=transport)
    client = _make_client(http_client=http)
    assert client._owns_http is False
    await client.aclose()
    # http is still usable (not closed by client)
    # Just confirm no exception; we don't rely on internal state.


@pytest.mark.asyncio
async def test_aclose_closes_owned_http():
    """When client creates its own http, aclose() closes it."""
    client = _make_client()  # no http_client arg → owned
    assert client._owns_http is True
    await client.aclose()
    # Confirm subsequent calls would fail because http is closed
    assert client._http.is_closed
