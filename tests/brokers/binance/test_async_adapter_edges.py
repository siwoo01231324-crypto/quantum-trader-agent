"""Edge coverage tests for AsyncBinanceFuturesAdapter.

Targets uncovered branches in src/brokers/binance/async_adapter.py:
- client_id_mod.generate fallback path when client_order_id fails regex (90-96)
- stream_fills() builds AsyncBinanceUserDataStream (179-185)
- ensure_position_mode mismatch raises BrokerStartupError (211-218)
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.brokers.base import MarginType, OrderRequest, OrderType, PositionSide
from src.brokers.binance.async_adapter import AsyncBinanceFuturesAdapter
from src.brokers.binance.schemas import PlaceOrderResponse
from src.brokers.errors import BrokerStartupError
from src.execution.base import Side, TimeInForce


def _make_adapter(kill_switch=None) -> AsyncBinanceFuturesAdapter:
    adapter = AsyncBinanceFuturesAdapter(
        api_key="k",
        secret="s",
        base_url="https://fapi.binance.test",
        ws_base_url="wss://fstream.binance.test/ws",
        paper=True,
        kill_switch=kill_switch,
    )
    # Swap out internal client with a fully mocked one
    adapter._client = MagicMock()
    adapter._client._now_ms = MagicMock(return_value=1700000000000)
    adapter._client._rate_limiter = MagicMock()
    adapter._client._rate_limiter.acquire = AsyncMock()
    return adapter


@pytest.mark.asyncio
async def test_place_order_with_invalid_client_id_regenerates():
    """If client_order_id fails the Binance regex, we fall back to generate()."""
    adapter = _make_adapter()
    # place_order stub returns a valid response
    adapter._client.place_order = AsyncMock(
        return_value=PlaceOrderResponse(
            orderId=1,
            clientOrderId="generated-cid",
            symbol="BTCUSDT",
            status="NEW",
            updateTime=1700000000000,
            origQty=Decimal("1"),
            price=Decimal("50000"),
        )
    )
    req = OrderRequest(
        client_order_id="바로-한글-넣으면-regex-실패",  # Invalid for Binance regex
        symbol="BTCUSDT",
        side=Side.BUY,
        qty=Decimal("1"),
        order_type=OrderType.LIMIT,
        price=Decimal("50000"),
        tif=TimeInForce.GTC,
        position_side=PositionSide.BOTH,
    )
    ack = await adapter.place_order(req)
    assert ack.broker_order_id == "1"
    # The place_order was called with a REGENERATED client id, not the invalid input
    call_args = adapter._client.place_order.await_args
    assert call_args is not None
    _, passed_cid = call_args[0]  # positional args: (req, cid)
    assert passed_cid != req.client_order_id  # regenerated
    assert len(passed_cid) > 0


@pytest.mark.asyncio
async def test_stream_fills_returns_async_iterator():
    """stream_fills() constructs the WS stream and returns an AsyncIterator."""
    adapter = _make_adapter()
    it = adapter.stream_fills()
    # Must be an async iterator (has __anext__)
    assert hasattr(it, "__anext__")


@pytest.mark.asyncio
async def test_ensure_position_mode_mismatch_raises():
    """ensure_position_mode raises BrokerStartupError on mismatch."""
    adapter = _make_adapter()
    adapter._client.get_position_mode = AsyncMock(return_value=False)
    with pytest.raises(BrokerStartupError, match="Position mode mismatch"):
        await adapter.ensure_position_mode(hedge=True)


@pytest.mark.asyncio
async def test_ensure_position_mode_matches_succeeds():
    adapter = _make_adapter()
    adapter._client.get_position_mode = AsyncMock(return_value=True)
    await adapter.ensure_position_mode(hedge=True)
    assert adapter._hedge_mode is True
