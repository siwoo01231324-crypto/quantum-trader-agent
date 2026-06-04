"""Unit tests for src.brokers.bitget.async_adapter.AsyncBitgetFuturesAdapter.

Mocks the underlying AsyncBitgetFuturesClient. Verifies:
  - place_order side/orderType mapping (BUY → "buy", LIMIT → "limit")
  - one-way vs hedge mode tradeSide insertion
  - max-notional cooldown (40762 trigger / skip / opposite side untouched)
  - get_positions holdSide → PositionSide
  - cancel/get_order forwarding
"""
from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.brokers.base import (
    MarginType,
    OrderRequest,
    OrderType,
    PositionSide,
)
from src.brokers.bitget.async_adapter import AsyncBitgetFuturesAdapter
from src.brokers.bitget.schemas import (
    OrderDetailResponse,
    PlaceOrderResponse,
    PositionResponse,
)
from src.brokers.errors import InvalidOrderError
from src.execution.base import Side, TimeInForce


def _make_adapter(*, paper: bool = True) -> AsyncBitgetFuturesAdapter:
    ad = AsyncBitgetFuturesAdapter.__new__(AsyncBitgetFuturesAdapter)
    ad.paper = paper
    ad._kill_switch = None
    ad._closing = False
    ad._product_type = "USDT-FUTURES"
    ad._max_notional_cooldown = {}
    ad._MAX_NOTIONAL_COOLDOWN_SEC = 300.0
    ad._hedge_mode = None
    cli = MagicMock()
    cli.place_order = AsyncMock()
    cli.cancel_order = AsyncMock()
    cli.get_order_detail = AsyncMock()
    cli.get_all_positions = AsyncMock(return_value=[])
    cli.get_single_position = AsyncMock(return_value=[])
    cli.get_account = AsyncMock()
    cli.set_leverage = AsyncMock()
    cli.set_margin_mode = AsyncMock()
    cli.set_position_mode = AsyncMock()
    cli.ping = AsyncMock()
    cli.aclose = AsyncMock()
    ad._client = cli
    sf = MagicMock()
    sf.lot_step.return_value = Decimal("0.001")
    sf.min_qty.return_value = Decimal("0.001")
    sf.tick_size.return_value = Decimal("0.1")
    sf.quantize_price = lambda s, p: p
    ad._symbol_filters = sf
    return ad


def _mk_req(side: Side, *, position_side=PositionSide.BOTH,
            reduce_only=False, qty="0.005",
            order_type=OrderType.MARKET, price=None) -> OrderRequest:
    return OrderRequest(
        client_order_id="bitget0001",
        symbol="BTCUSDT",
        side=side,
        qty=Decimal(qty),
        order_type=order_type,
        price=Decimal(price) if price else None,
        tif=TimeInForce.GTC,
        position_side=position_side,
        reduce_only=reduce_only,
    )


@pytest.mark.asyncio
async def test_place_order_one_way_mode_omits_trade_side():
    ad = _make_adapter()
    ad._client.place_order.return_value = PlaceOrderResponse(orderId="1", clientOid="bitget0001")
    await ad.place_order(_mk_req(Side.BUY))
    call_kwargs = ad._client.place_order.call_args.kwargs
    assert call_kwargs["side"] == "buy"
    assert call_kwargs["order_type"] == "market"
    assert call_kwargs["trade_side"] is None  # one-way → no tradeSide
    assert call_kwargs["reduce_only"] is False


@pytest.mark.asyncio
async def test_place_order_hedge_mode_open_long():
    ad = _make_adapter()
    ad._client.place_order.return_value = PlaceOrderResponse(orderId="1", clientOid="c")
    await ad.place_order(_mk_req(Side.BUY, position_side=PositionSide.LONG))
    assert ad._client.place_order.call_args.kwargs["trade_side"] == "open"


@pytest.mark.asyncio
async def test_place_order_hedge_mode_close_long():
    ad = _make_adapter()
    ad._client.place_order.return_value = PlaceOrderResponse(orderId="1", clientOid="c")
    await ad.place_order(_mk_req(Side.SELL, position_side=PositionSide.LONG, reduce_only=True))
    assert ad._client.place_order.call_args.kwargs["trade_side"] == "close"


@pytest.mark.asyncio
async def test_place_order_limit_includes_price():
    ad = _make_adapter()
    ad._client.place_order.return_value = PlaceOrderResponse(orderId="1", clientOid="c")
    await ad.place_order(_mk_req(Side.BUY, order_type=OrderType.LIMIT, price="50000"))
    assert ad._client.place_order.call_args.kwargs["price"] == Decimal("50000")


@pytest.mark.asyncio
async def test_place_order_market_omits_price():
    ad = _make_adapter()
    ad._client.place_order.return_value = PlaceOrderResponse(orderId="1", clientOid="c")
    await ad.place_order(_mk_req(Side.BUY, order_type=OrderType.MARKET))
    assert ad._client.place_order.call_args.kwargs["price"] is None


# ── max-notional cooldown (40762 trigger) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_40762_registers_cooldown_and_reraises():
    ad = _make_adapter()
    ad._client.place_order.side_effect = InvalidOrderError(
        "[40762] order qty exceeds upper limit"
    )
    with pytest.raises(InvalidOrderError, match=r"\[40762\]"):
        await ad.place_order(_mk_req(Side.SELL))
    # cooldown key uses req.side.value which is uppercase ("BUY"/"SELL")
    assert ("BTCUSDT", "SELL") in ad._max_notional_cooldown


@pytest.mark.asyncio
async def test_40774_does_not_trigger_cooldown():
    # 40774 is position-mode mismatch — adapter must NOT register a max-notional
    # cooldown for this (regression: original draft incorrectly mapped it).
    ad = _make_adapter()
    ad._client.place_order.side_effect = InvalidOrderError(
        "[40774] The order type for unilateral position must be unilateral"
    )
    with pytest.raises(InvalidOrderError):
        await ad.place_order(_mk_req(Side.BUY))
    assert ("BTCUSDT", "BUY") not in ad._max_notional_cooldown


@pytest.mark.asyncio
async def test_cooldown_skips_subsequent_same_side():
    ad = _make_adapter()
    ad._client.place_order.side_effect = InvalidOrderError("[40762] cap")
    with pytest.raises(InvalidOrderError):
        await ad.place_order(_mk_req(Side.SELL))
    ad._client.place_order.reset_mock()

    ack = await ad.place_order(_mk_req(Side.SELL))
    assert ack.status == "REJECTED"
    assert ack.broker_order_id == ""
    assert ad._client.place_order.await_count == 0


@pytest.mark.asyncio
async def test_cooldown_does_not_block_opposite_side():
    ad = _make_adapter()
    ad._client.place_order.side_effect = InvalidOrderError("[40762] cap")
    with pytest.raises(InvalidOrderError):
        await ad.place_order(_mk_req(Side.SELL))
    ad._client.place_order.reset_mock()
    ad._client.place_order.side_effect = None
    ad._client.place_order.return_value = PlaceOrderResponse(orderId="x", clientOid="c")

    ack = await ad.place_order(_mk_req(Side.BUY, reduce_only=True))
    assert ack.status == "NEW"
    assert ad._client.place_order.await_count == 1


@pytest.mark.asyncio
async def test_cooldown_expires_after_timeout_re_invokes_exchange():
    ad = _make_adapter()
    ad._client.place_order.side_effect = InvalidOrderError("[40762] cap")
    with pytest.raises(InvalidOrderError):
        await ad.place_order(_mk_req(Side.SELL))
    # expire the cooldown (key uppercase per Side enum value)
    ad._max_notional_cooldown[("BTCUSDT", "SELL")] = time.monotonic() - 1
    ad._client.place_order.reset_mock()
    ad._client.place_order.side_effect = InvalidOrderError("[40762] still over")
    with pytest.raises(InvalidOrderError):
        await ad.place_order(_mk_req(Side.SELL))
    assert ad._client.place_order.await_count == 1


# ── positions / cancel / get_order ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_positions_skips_zero_total():
    ad = _make_adapter()
    ad._client.get_all_positions.return_value = [
        PositionResponse(
            symbol="BTCUSDT", holdSide="long", total=Decimal("0"),
            available=Decimal("0"), averageOpenPrice=Decimal("0"),
            markPrice=Decimal("0"), leverage=1, marginMode="crossed",
            unrealizedPL=Decimal("0"), liquidationPrice=None, marginCoin="USDT",
        ),
        PositionResponse(
            symbol="ETHUSDT", holdSide="short", total=Decimal("2"),
            available=Decimal("2"), averageOpenPrice=Decimal("3000"),
            markPrice=Decimal("2900"), leverage=10, marginMode="crossed",
            unrealizedPL=Decimal("200"), liquidationPrice=Decimal("4000"),
            marginCoin="USDT",
        ),
    ]
    poss = await ad.get_positions()
    assert len(poss) == 1
    assert poss[0].symbol == "ETHUSDT"
    assert poss[0].side == PositionSide.SHORT
    assert poss[0].qty == Decimal("2")


@pytest.mark.asyncio
async def test_cancel_order_forwards_to_client():
    ad = _make_adapter()
    await ad.cancel_order(symbol="BTCUSDT", broker_order_id="abc")
    ad._client.cancel_order.assert_awaited_once_with(
        symbol="BTCUSDT", order_id="abc", client_oid=None,
    )


@pytest.mark.asyncio
async def test_get_order_status_mapping():
    ad = _make_adapter()
    ad._client.get_order_detail.return_value = OrderDetailResponse(
        orderId="x", clientOid="c", symbol="BTCUSDT",
        size=Decimal("0.01"), price=Decimal("50000"), priceAvg=None,
        status="live", side="buy", orderType="limit",
        filledSize=Decimal("0"), ctime=1700000000000, utime=1700000001000,
    )
    ack = await ad.get_order(symbol="BTCUSDT", broker_order_id="x")
    assert ack.status == "NEW"   # "live" → NEW

    ad._client.get_order_detail.return_value = OrderDetailResponse(
        orderId="x", clientOid="c", symbol="BTCUSDT",
        size=Decimal("0.01"), price=None, priceAvg=Decimal("50100"),
        status="filled", side="buy", orderType="market",
        filledSize=Decimal("0.01"), ctime=0, utime=0,
    )
    ack = await ad.get_order(symbol="BTCUSDT", broker_order_id="x")
    assert ack.status == "FILLED"
    assert ack.filled_qty == Decimal("0.01")


@pytest.mark.asyncio
async def test_ensure_margin_type_maps_to_bitget_string():
    ad = _make_adapter()
    await ad.ensure_margin_type("BTCUSDT", MarginType.CROSSED)
    ad._client.set_margin_mode.assert_awaited_once()
    assert ad._client.set_margin_mode.call_args.kwargs["mode"] == "crossed"

    ad._client.set_margin_mode.reset_mock()
    await ad.ensure_margin_type("BTCUSDT", MarginType.ISOLATED)
    assert ad._client.set_margin_mode.call_args.kwargs["mode"] == "isolated"


@pytest.mark.asyncio
async def test_ensure_position_mode_caches_and_skips_redundant():
    ad = _make_adapter()
    await ad.ensure_position_mode(hedge=False)
    assert ad._hedge_mode is False
    assert ad._client.set_position_mode.await_count == 1
    # Second call with same target: skipped.
    await ad.ensure_position_mode(hedge=False)
    assert ad._client.set_position_mode.await_count == 1


@pytest.mark.asyncio
async def test_stream_fills_raises_not_implemented_in_p1():
    ad = _make_adapter()
    with pytest.raises(NotImplementedError, match=r"Phase 2"):
        ad.stream_fills()
