"""Bug A — AsyncBinanceFuturesAdapter must quantize req.qty to the real
exchangeInfo LOT_SIZE stepSize before submitting (#238 follow-up).

Root incident: live-scanner placed TRX/KITE orders with qty quantized only to
the conversion-layer 0.001 fallback (832.840 / 1373.141). Binance LOT_SIZE for
those perps is coarser (e.g. 1) → exchange rejects every order with -1111
"Precision is over the maximum defined for this asset" → "doesn't buy".

The authoritative fix is adapter-side (real exchange filter, covers ALL
symbols, not a whitelist): floor req.qty DOWN to SymbolFilters.lot_step(symbol)
right before _client.place_order. Sub-minQty → do NOT submit (raise, so the
executor down-grades to a REJECTED ack — never flood the exchange with a
guaranteed-reject). Unknown symbol / exchangeInfo failure → safe fallback:
keep current qty + log, never crash the order path. Whitelisted majors stay
byte-identical.

Network-zero: HTTP mocked via respx; SymbolFilters stubbed in-process.
"""
from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx

from src.brokers.base import OrderRequest, OrderType
from src.brokers.binance.async_adapter import AsyncBinanceFuturesAdapter
from src.brokers.errors import InvalidOrderError
from src.execution.base import Side, TimeInForce

BASE_URL = "https://fapi.binance.com"


class _StubFilters:
    """In-process stand-in for SymbolFilters with controllable step/min.

    raises=True simulates exchangeInfo unavailable / unknown symbol.
    """

    def __init__(self, step: Decimal, min_qty: Decimal, raises: bool = False):
        self._step = step
        self._min_qty = min_qty
        self._raises = raises

    def lot_step(self, symbol: str) -> Decimal:
        if self._raises:
            from src.brokers.errors import ValidationError

            raise ValidationError(f"Unknown symbol: {symbol}")
        return self._step

    def min_qty(self, symbol: str) -> Decimal:
        if self._raises:
            from src.brokers.errors import ValidationError

            raise ValidationError(f"Unknown symbol: {symbol}")
        return self._min_qty

    def quantize_qty(self, symbol: str, qty: Decimal) -> Decimal:
        if self._raises:
            from src.brokers.errors import ValidationError

            raise ValidationError(f"Unknown symbol: {symbol}")
        floored = (qty // self._step) * self._step
        return floored.quantize(self._step)


def _make_adapter(filters: _StubFilters | None = None) -> AsyncBinanceFuturesAdapter:
    adapter = AsyncBinanceFuturesAdapter(
        api_key="testkey",
        secret="testsecret",
        base_url=BASE_URL,
    )
    if filters is not None:
        adapter._symbol_filters = filters
    return adapter


def _req(symbol: str, qty: Decimal, coid: str = "live-rsi-x") -> OrderRequest:
    return OrderRequest(
        client_order_id=coid,
        symbol=symbol,
        side=Side.BUY,
        qty=qty,
        order_type=OrderType.MARKET,
        price=None,
        tif=TimeInForce.GTC,
    )


def _mock_time():
    respx.get(f"{BASE_URL}/fapi/v1/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1700000000000})
    )


def _capture_order_route(symbol: str):
    return respx.post(f"{BASE_URL}/fapi/v1/order").mock(
        return_value=httpx.Response(
            200,
            json={
                "orderId": 1,
                "clientOrderId": "live-rsi-x",
                "symbol": symbol,
                "status": "FILLED",
                "origQty": "0",
                "price": "0",
                "avgPrice": "1",
                "updateTime": 1700000001000,
            },
        )
    )


def _submitted_qty(route) -> str:
    """Extract the 'quantity' form param from the last captured request."""
    req = route.calls.last.request
    body = req.content.decode()
    from urllib.parse import parse_qs

    return parse_qs(body)["quantity"][0]


# ── A1: coarse stepSize (TRX/KITE-like) → qty floored to real step ─────────────

@pytest.mark.asyncio
@respx.mock
async def test_coarse_step_size_floors_qty_not_left_at_0_001():
    """TRX-like: conversion gave 832.840 (0.001 step); real LOT_SIZE step=1.
    Adapter must submit '832', NOT '832.840' (which Binance rejects -1111).
    """
    _mock_time()
    route = _capture_order_route("TRXUSDT")
    adapter = _make_adapter(_StubFilters(step=Decimal("1"), min_qty=Decimal("1")))

    await adapter.place_order(_req("TRXUSDT", Decimal("832.840")))

    assert _submitted_qty(route) == "832"


@pytest.mark.asyncio
@respx.mock
async def test_coarse_step_size_kite_like_1373():
    _mock_time()
    route = _capture_order_route("KITEUSDT")
    adapter = _make_adapter(_StubFilters(step=Decimal("1"), min_qty=Decimal("1")))

    await adapter.place_order(_req("KITEUSDT", Decimal("1373.141")))

    assert _submitted_qty(route) == "1373"


@pytest.mark.asyncio
@respx.mock
async def test_fractional_step_floor_preserved():
    """step=0.01 → 12.3456 floors to 12.34 (not the 0.001 conversion default)."""
    _mock_time()
    route = _capture_order_route("ADAUSDT")
    adapter = _make_adapter(_StubFilters(step=Decimal("0.01"), min_qty=Decimal("0.01")))

    await adapter.place_order(_req("ADAUSDT", Decimal("12.3456")))

    assert Decimal(_submitted_qty(route)) == Decimal("12.34")


# ── A2: sub-minQty after floor → do NOT submit (no exchange flood) ─────────────

@pytest.mark.asyncio
@respx.mock
async def test_sub_min_qty_after_floor_is_dropped_not_submitted():
    """qty 0.7 with step=1 floors to 0 (< minQty 1). Submitting would be a
    guaranteed -1111/-4164 reject → the #238 flood. Must raise instead so the
    executor down-grades to a REJECTED ack; no POST hits the exchange.
    """
    _mock_time()
    route = _capture_order_route("TRXUSDT")
    adapter = _make_adapter(_StubFilters(step=Decimal("1"), min_qty=Decimal("1")))

    with pytest.raises(InvalidOrderError):
        await adapter.place_order(_req("TRXUSDT", Decimal("0.7")))

    assert route.call_count == 0  # never reached the exchange


@pytest.mark.asyncio
@respx.mock
async def test_qty_below_min_qty_but_nonzero_dropped():
    """Floors to a positive multiple still under minQty → also dropped."""
    _mock_time()
    route = _capture_order_route("TRXUSDT")
    adapter = _make_adapter(_StubFilters(step=Decimal("1"), min_qty=Decimal("5")))

    with pytest.raises(InvalidOrderError):
        await adapter.place_order(_req("TRXUSDT", Decimal("3.9")))  # floors to 3 < 5

    assert route.call_count == 0


# ── A3: unknown symbol / exchangeInfo failure → safe fallback ─────────────────

@pytest.mark.asyncio
@respx.mock
async def test_unknown_symbol_safe_fallback_keeps_qty_and_submits():
    """SymbolFilters can't resolve → keep current behaviour (current qty),
    log, do NOT crash the order path.
    """
    _mock_time()
    route = _capture_order_route("WEIRDUSDT")
    adapter = _make_adapter(
        _StubFilters(step=Decimal("1"), min_qty=Decimal("1"), raises=True)
    )

    ack = await adapter.place_order(_req("WEIRDUSDT", Decimal("123.456")))

    assert route.call_count == 1
    assert _submitted_qty(route) == "123.456"  # unchanged
    assert ack.status == "FILLED"


# ── A4: whitelisted majors unchanged ──────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_whitelisted_btc_already_aligned_byte_identical():
    """BTCUSDT step 0.001, qty 0.001 already aligned → submitted byte-identical."""
    _mock_time()
    route = _capture_order_route("BTCUSDT")
    adapter = _make_adapter(
        _StubFilters(step=Decimal("0.001"), min_qty=Decimal("0.001"))
    )

    await adapter.place_order(_req("BTCUSDT", Decimal("0.001")))

    assert _submitted_qty(route) == "0.001"
