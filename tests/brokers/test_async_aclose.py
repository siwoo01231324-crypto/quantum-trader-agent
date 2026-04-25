"""C7: aclose 5-stage contract tests.

Plan §2 Step 5 / Acceptance:
  1. aclose during new place_order → BrokerClosedError
  2. aclose while inflight REST → CancelledError propagation
  3. aclose → listenKey keepalive task cancelled (Binance only)
  4. KIS aclose skips step 3 (no keepalive task), runs step 2→4→5

These tests use mocks so they run without a real exchange server.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.brokers.base import (
    Balance,
    HealthStatus,
    MarginType,
    OrderAck,
    OrderRequest,
    OrderType,
    Position,
    PositionSide,
)
from src.brokers.errors import BrokerClosedError
from src.brokers.types import BrokerFill
from src.execution.base import Side, TimeInForce


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_order_req(symbol: str = "BTCUSDT") -> OrderRequest:
    return OrderRequest(
        client_order_id="test-001",
        symbol=symbol,
        side=Side.BUY,
        qty=Decimal("0.01"),
        order_type=OrderType.MARKET,
        price=None,
        tif=TimeInForce.GTC,
    )


# ---------------------------------------------------------------------------
# Binance aclose tests
# ---------------------------------------------------------------------------

class TestBinanceAclose:
    """Tests for AsyncBinanceFuturesAdapter.aclose() 5-stage contract."""

    def _make_adapter(self):
        """Create a Binance adapter with mocked HTTP client."""
        from src.brokers.binance.async_adapter import AsyncBinanceFuturesAdapter

        adapter = AsyncBinanceFuturesAdapter(
            api_key="test-key",
            secret="test-secret",
            base_url="https://testnet.binancefuture.com",
            ws_base_url="wss://stream.binancefuture.com",
            paper=True,
        )
        # Inject mock HTTP client
        mock_client = MagicMock()
        mock_client.aclose = AsyncMock()
        mock_client.ping = AsyncMock()
        mock_client._rate_limiter = MagicMock()
        mock_client._rate_limiter.acquire = AsyncMock()
        mock_client._rate_limiter.on_response_headers = MagicMock()
        mock_client._now_ms = MagicMock(return_value=1000000)
        adapter._client = mock_client
        return adapter

    @pytest.mark.asyncio
    async def test_place_order_raises_broker_closed_error_after_aclose(self):
        """Stage 1: after aclose(), place_order must raise BrokerClosedError."""
        adapter = self._make_adapter()
        await adapter.aclose()

        with pytest.raises(BrokerClosedError):
            await adapter.place_order(_make_order_req())

    @pytest.mark.asyncio
    async def test_aclose_is_idempotent(self):
        """aclose() called twice must not raise."""
        adapter = self._make_adapter()
        await adapter.aclose()
        await adapter.aclose()  # second call must be a no-op

    @pytest.mark.asyncio
    async def test_aclose_closes_httpx_client(self):
        """Stage 5: aclose() must call httpx AsyncClient.aclose()."""
        adapter = self._make_adapter()
        await adapter.aclose()
        adapter._client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_aclose_sets_closing_flag(self):
        """Stage 1: _closing flag must be True after aclose()."""
        adapter = self._make_adapter()
        assert not adapter._closing
        await adapter.aclose()
        assert adapter._closing

    @pytest.mark.asyncio
    async def test_aclose_cancels_keepalive_task_if_stream_active(self):
        """Stage 3: if stream_fills() was called (keepalive task exists), aclose cancels it."""
        from src.brokers.binance.async_adapter import AsyncBinanceFuturesAdapter
        from src.brokers.binance.async_ws import AsyncBinanceUserDataStream

        adapter = self._make_adapter()

        # Create a mock ws_stream with a mock aclose
        mock_ws_stream = MagicMock()
        mock_ws_stream.aclose = AsyncMock()
        mock_ws_stream._listen_key_mgr = MagicMock()
        mock_ws_stream._listen_key_mgr.stop_keepalive = AsyncMock()
        mock_ws_stream._listen_key_mgr.delete = AsyncMock()
        mock_ws_stream._listen_key_mgr._keepalive_task = None

        adapter._ws_stream = mock_ws_stream
        await adapter.aclose()

        mock_ws_stream.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_listen_key_manager_keepalive_task_cancelled_on_stop(self):
        """Stage 3: ListenKeyManager.stop_keepalive() cancels and awaits the task."""
        from src.brokers.binance.listen_key import ListenKeyManager

        mock_client = MagicMock()
        mock_client.issue_listen_key = AsyncMock(return_value="test-listen-key-abc")
        mock_client.extend_listen_key = AsyncMock()

        mgr = ListenKeyManager(mock_client)
        expiry_event = asyncio.Event()

        await mgr.issue()
        mgr.start_keepalive(expiry_event)

        # Task should be running
        assert mgr._keepalive_task is not None
        assert not mgr._keepalive_task.done()

        await mgr.stop_keepalive()

        # After stop, task must be done (cancelled)
        assert mgr._keepalive_task is None

    @pytest.mark.asyncio
    async def test_place_order_blocked_while_closing(self):
        """Concurrent: set _closing=True then place_order must raise immediately."""
        adapter = self._make_adapter()
        adapter._closing = True  # simulate closing in progress

        with pytest.raises(BrokerClosedError):
            await adapter.place_order(_make_order_req())

    @pytest.mark.asyncio
    async def test_aclose_cancels_inflight_tasks(self):
        """Stage 4: inflight REST tasks in _inflight list are cancelled and awaited."""
        adapter = self._make_adapter()

        # Add a long-running task to simulate inflight REST
        async def long_running():
            await asyncio.sleep(100)

        task = asyncio.get_event_loop().create_task(long_running())
        adapter._inflight.append(task)

        await adapter.aclose()

        # Task must be cancelled after aclose
        assert task.cancelled() or task.done(), "Inflight task must be cancelled by aclose"

    @pytest.mark.asyncio
    async def test_aclose_clears_inflight_after_cancel(self):
        """Stage 4: _inflight list must be empty after aclose."""
        adapter = self._make_adapter()

        async def long_running():
            await asyncio.sleep(100)

        task = asyncio.get_event_loop().create_task(long_running())
        adapter._inflight.append(task)

        await adapter.aclose()
        assert len(adapter._inflight) == 0, "_inflight must be cleared after aclose"


# ---------------------------------------------------------------------------
# KIS aclose tests
# ---------------------------------------------------------------------------

class TestKISAclose:
    """Tests for KISAsyncAdapter.aclose() — step 3 is no-op (no listenKey task)."""

    def _make_adapter(self):
        from src.brokers.kis.async_adapter import KISAsyncAdapter

        # Patch KISAuth and clients to avoid real network
        with patch("src.brokers.kis.async_adapter.KISAuth") as mock_auth_cls, \
             patch("src.brokers.kis.async_adapter.KISAsyncClient") as mock_client_cls, \
             patch("src.brokers.kis.async_adapter.KISAsyncWebSocket") as mock_ws_cls:

            mock_auth_cls.return_value = MagicMock()
            mock_client_cls.return_value = MagicMock(aclose=AsyncMock())
            mock_ws_cls.return_value = MagicMock(aclose=AsyncMock())

            adapter = KISAsyncAdapter(
                app_key="test-key",
                app_secret="test-secret",
                hts_id="TEST0000",
                credit_number="12345678-01",
                paper=True,
            )

        return adapter

    @pytest.mark.asyncio
    async def test_kis_place_order_raises_broker_closed_error_after_aclose(self):
        """Stage 1: KIS aclose() sets _closing=True → place_order raises BrokerClosedError."""
        adapter = self._make_adapter()
        await adapter.aclose()

        with pytest.raises(BrokerClosedError):
            await adapter.place_order(_make_order_req("005930"))

    @pytest.mark.asyncio
    async def test_kis_aclose_is_idempotent(self):
        """KIS aclose() called twice must not raise."""
        adapter = self._make_adapter()
        await adapter.aclose()
        await adapter.aclose()

    @pytest.mark.asyncio
    async def test_kis_aclose_closes_ws_and_http(self):
        """KIS aclose steps 2 and 5: WS aclose then HTTP client aclose."""
        adapter = self._make_adapter()

        ws_aclose_called = []
        http_aclose_called = []

        async def ws_aclose():
            ws_aclose_called.append(1)

        async def http_aclose():
            http_aclose_called.append(1)

        adapter._ws.aclose = ws_aclose
        adapter._client.aclose = http_aclose

        await adapter.aclose()

        assert len(ws_aclose_called) == 1, "WS aclose must be called (step 2)"
        assert len(http_aclose_called) == 1, "HTTP client aclose must be called (step 5)"

    @pytest.mark.asyncio
    async def test_kis_aclose_has_no_keepalive_task(self):
        """KIS step 3 must be skipped: no keepalive task attribute."""
        adapter = self._make_adapter()
        # KIS adapter should not have a _keepalive_task attribute
        assert not hasattr(adapter, "_keepalive_task"), \
            "KIS adapter must not have a keepalive task (step 3 is no-op)"

    @pytest.mark.asyncio
    async def test_kis_aclose_ws_before_http(self):
        """KIS aclose order: WS (step 2) must happen before httpx aclose (step 5)."""
        adapter = self._make_adapter()

        call_order = []

        async def ws_aclose():
            call_order.append("ws")

        async def http_aclose():
            call_order.append("http")

        adapter._ws.aclose = ws_aclose
        adapter._client.aclose = http_aclose

        await adapter.aclose()

        assert call_order == ["ws", "http"], \
            f"Expected ws before http, got: {call_order}"
