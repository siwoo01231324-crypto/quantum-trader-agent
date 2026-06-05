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
from src.brokers.errors import BrokerStartupError, InvalidOrderError
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


# ── -2027 max-notional cooldown (2026-06-03) ──────────────────────────────────
# testnet 의 종목별 maxNotionalValue 한도(VVVUSDT=25,000) 를 넘으면 -2027 거부.
# (sym, side) cooldown 으로 동일 발주가 매 1m tick 거래소까지 도달해 6000/min
# rate-limit → IP ban → 다른 종목까지 마비되는 폭주를 차단.


def _mk_market_req(side: Side, *, reduce_only: bool = False,
                   cid: str = "regression0001") -> OrderRequest:
    return OrderRequest(
        client_order_id=cid,
        symbol="VVVUSDT",
        side=side,
        qty=Decimal("6.95"),
        order_type=OrderType.MARKET,
        price=None,
        tif=TimeInForce.GTC,
        position_side=PositionSide.BOTH,
        reduce_only=reduce_only,
    )


@pytest.mark.asyncio
async def test_max_notional_2027_registers_cooldown_and_reraises():
    adapter = _make_adapter()
    adapter._client.place_order = AsyncMock(
        side_effect=InvalidOrderError(
            "[-2027] Exceeded the maximum allowable position at current leverage."
        )
    )
    # symbol filters: any quantization succeeds
    adapter._symbol_filters = MagicMock()
    adapter._symbol_filters.lot_step.return_value = Decimal("0.01")
    adapter._symbol_filters.min_qty.return_value = Decimal("0.01")

    with pytest.raises(InvalidOrderError, match=r"\[-2027\]"):
        await adapter.place_order(_mk_market_req(Side.SELL))

    assert ("VVVUSDT", "SELL") in adapter._max_notional_cooldown


@pytest.mark.asyncio
async def test_max_notional_cooldown_skips_subsequent_call():
    """During cooldown, identical (sym, side) returns local REJECTED ack — no exchange."""
    adapter = _make_adapter()
    adapter._client.place_order = AsyncMock(
        side_effect=InvalidOrderError("[-2027] cap reached")
    )
    adapter._symbol_filters = MagicMock()
    adapter._symbol_filters.lot_step.return_value = Decimal("0.01")
    adapter._symbol_filters.min_qty.return_value = Decimal("0.01")

    # 1st call → cooldown registered
    with pytest.raises(InvalidOrderError):
        await adapter.place_order(_mk_market_req(Side.SELL))
    adapter._client.place_order.reset_mock()

    # 2nd call → locally REJECTED, exchange not touched
    ack = await adapter.place_order(_mk_market_req(Side.SELL))
    assert ack.status == "REJECTED"
    assert ack.broker_order_id == ""
    assert adapter._client.place_order.await_count == 0


@pytest.mark.asyncio
async def test_max_notional_cooldown_does_not_block_opposite_side():
    """SELL cooldown must NOT block BUY reduce-only (exit) on same symbol."""
    adapter = _make_adapter()
    adapter._client.place_order = AsyncMock(
        side_effect=InvalidOrderError("[-2027] cap reached")
    )
    adapter._symbol_filters = MagicMock()
    adapter._symbol_filters.lot_step.return_value = Decimal("0.01")
    adapter._symbol_filters.min_qty.return_value = Decimal("0.01")

    with pytest.raises(InvalidOrderError):
        await adapter.place_order(_mk_market_req(Side.SELL))
    # SELL on cooldown, but BUY (reduce) must reach exchange
    adapter._client.place_order.reset_mock()
    adapter._client.place_order.side_effect = None
    adapter._client.place_order.return_value = PlaceOrderResponse(
        orderId=42, clientOrderId="cid", symbol="VVVUSDT", status="NEW",
        updateTime=1700000000000, origQty=Decimal("6.95"), price=Decimal("0"),
    )
    ack = await adapter.place_order(
        _mk_market_req(Side.BUY, reduce_only=True, cid="reduce0001")
    )
    assert ack.status == "NEW"
    assert adapter._client.place_order.await_count == 1
