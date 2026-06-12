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
    ad._leverage_forced = {}  # #380 ensure_leverage_target 캐시
    ad._native_tpsl_symbols = set()  # P2 — preset TP/SL 활성 종목(__new__ 우회라 명시)
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
async def test_40762_balance_does_not_register_cooldown():
    # 2026-06-12 ① — 40762 "exceeds the balance" = 일시적 잔고부족. 쿨다운 걸면
    # 그 종목이 300s 막혀 잔고 풀려도 재진입 못 함(03·23시 동시발화 누락 원인).
    # 거부는 되되 쿨다운은 X → 다음 발화에 잔고 있으면 자연 재시도.
    ad = _make_adapter()
    ad._client.place_order.side_effect = InvalidOrderError(
        "[40762] The order amount exceeds the balance"
    )
    with pytest.raises(InvalidOrderError):
        await ad.place_order(_mk_req(Side.SELL))
    assert ("BTCUSDT", "SELL") not in ad._max_notional_cooldown  # 쿨다운 안 걸림


@pytest.mark.asyncio
async def test_cooldown_skip_sets_reject_reason():
    # 2026-06-12 ② — 쿨다운으로 스킵된 주문 OrderAck 에 reject_reason 채워짐
    # (이전 None → WAL order_rejected reason 빈값 → 누락 추적 불가).
    ad = _make_adapter()
    ad._client.place_order.side_effect = InvalidOrderError(
        "[40762] order qty exceeds upper limit"
    )
    with pytest.raises(InvalidOrderError):
        await ad.place_order(_mk_req(Side.SELL))           # 쿨다운 등록
    ad._client.place_order.side_effect = None
    ack = await ad.place_order(_mk_req(Side.SELL))         # 쿨다운에 막힘
    assert ack.status == "REJECTED"
    assert ack.reject_reason and "MAX_NOTIONAL_COOLDOWN" in ack.reject_reason


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
async def test_stream_fills_returns_async_iterator():
    # 2026-06-05 P2 — stream_fills() now constructs AsyncBitgetUserDataStream.
    # We don't actually connect (no real WS), just verify the iterator surface.
    ad = _make_adapter()
    # _ws_creds set in _make_adapter via Inject (we don't have it normally).
    ad._ws_creds = ("k", "s", "p")
    ad._ws_paper = True
    ad._fill_queue_size = 100
    ad._overflow_policy = "block"
    it = ad.stream_fills()
    assert hasattr(it, "__anext__"), "stream_fills must return AsyncIterator"
    # Cleanup the lazy WS stream (no connection was made yet).
    ad._ws_stream._stop.set()


# ── #380: ensure_leverage_target — 강제 leverage + 캐시 ──────────────────────
@pytest.mark.asyncio
async def test_ensure_leverage_target_sets_and_caches():
    ad = _make_adapter()
    await ad.ensure_leverage_target("SHIBUSDT", 10)
    ad._client.set_leverage.assert_awaited_once_with(symbol="SHIBUSDT", leverage=10)
    # 동일 (symbol, leverage) 재요청은 캐시로 REST 생략
    ad._client.set_leverage.reset_mock()
    await ad.ensure_leverage_target("SHIBUSDT", 10)
    ad._client.set_leverage.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_leverage_target_overrides_existing_unlike_minimum():
    """minimum 과 달리 현재 leverage 와 무관하게 강제 set (override)."""
    ad = _make_adapter()
    # 이미 1x 포지션이 있어도 ensure_leverage_target 은 set 을 호출
    ad._client.get_single_position = AsyncMock(return_value=[MagicMock(leverage=1)])
    await ad.ensure_leverage_target("SHIBUSDT", 10)
    ad._client.set_leverage.assert_awaited_once_with(symbol="SHIBUSDT", leverage=10)


@pytest.mark.asyncio
async def test_ensure_leverage_target_new_value_re_sets():
    ad = _make_adapter()
    await ad.ensure_leverage_target("SHIBUSDT", 10)
    ad._client.set_leverage.reset_mock()
    await ad.ensure_leverage_target("SHIBUSDT", 20)  # 다른 값 → 재설정
    ad._client.set_leverage.assert_awaited_once_with(symbol="SHIBUSDT", leverage=20)


@pytest.mark.asyncio
async def test_ensure_leverage_target_failure_not_cached():
    """set 실패 시 캐시 안 채움 → 다음 발주에서 재시도 가능, 예외는 삼킴."""
    ad = _make_adapter()
    ad._client.set_leverage = AsyncMock(side_effect=InvalidOrderError("[xxxx] open position"))
    await ad.ensure_leverage_target("SHIBUSDT", 10)  # 예외 삼켜야 함
    assert "SHIBUSDT" not in ad._leverage_forced
    # 재시도
    ad._client.set_leverage = AsyncMock()
    await ad.ensure_leverage_target("SHIBUSDT", 10)
    ad._client.set_leverage.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_leverage_target_zero_is_noop():
    ad = _make_adapter()
    await ad.ensure_leverage_target("SHIBUSDT", 0)
    ad._client.set_leverage.assert_not_awaited()
