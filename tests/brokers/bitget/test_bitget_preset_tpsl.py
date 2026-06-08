"""거래소 네이티브 preset TP/SL (진입 주문 첨부, 2026-06-08) 단위테스트.

holdSide 불필요 — place-order body 에 presetStopSurplusPrice/presetStopLossPrice.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.brokers.base import OrderRequest, OrderType, Side, TimeInForce
from src.brokers.bitget.async_adapter import AsyncBitgetFuturesAdapter
from src.brokers.bitget.async_http import AsyncBitgetFuturesClient


# ── http layer: body 에 preset 필드 ──────────────────────────────────────────

class _CaptureClient(AsyncBitgetFuturesClient):
    def __init__(self):
        self._product_type = "USDT-FUTURES"
        self.body = None
    async def _request(self, method, path, *, params=None, body=None, **kw):
        self.body = body
        return {"orderId": "1", "clientOid": body.get("clientOid", "x")}


@pytest.mark.asyncio
async def test_http_place_order_adds_preset_fields():
    c = _CaptureClient()
    await c.place_order(
        symbol="BTCUSDT", side="sell", order_type="market",
        size=Decimal("1"), price=None, client_oid="c",
        preset_tp_price=Decimal("99"), preset_sl_price=Decimal("100.5"),
    )
    assert c.body["presetStopSurplusPrice"] == "99"
    assert c.body["presetStopLossPrice"] == "100.5"


@pytest.mark.asyncio
async def test_http_place_order_omits_preset_when_absent():
    c = _CaptureClient()
    await c.place_order(
        symbol="BTCUSDT", side="sell", order_type="market",
        size=Decimal("1"), price=None, client_oid="c",
    )
    assert "presetStopSurplusPrice" not in c.body
    assert "presetStopLossPrice" not in c.body


# ── adapter layer: 게이트(env)·reduce_only·양자화 ────────────────────────────

class _IdFilters:
    def lot_step(self, s): return Decimal("0.001")
    def min_qty(self, s): return Decimal("0.001")
    def quantize_price(self, s, p): return Decimal(str(p))  # identity


class _SpyClient:
    def __init__(self): self.kw = None
    async def place_order(self, **kw):
        self.kw = kw
        from src.brokers.bitget.schemas import PlaceOrderResponse
        return PlaceOrderResponse.from_json({"orderId": "1", "clientOid": "c"})


def _adapter():
    a = AsyncBitgetFuturesAdapter.__new__(AsyncBitgetFuturesAdapter)
    a._client = _SpyClient()
    a._symbol_filters = _IdFilters()
    a._max_notional_cooldown = {}
    a._closing = False
    a._kill_switch = None
    return a


def _req(reduce_only=False):
    return OrderRequest(
        client_order_id="abcdef1234", symbol="BTCUSDT", side=Side.SELL,
        qty=Decimal("1"), order_type=OrderType.MARKET, price=None,
        tif=TimeInForce.IOC, reduce_only=reduce_only,
        preset_tp_price=Decimal("99"), preset_sl_price=Decimal("100.5"),
    )


@pytest.mark.asyncio
async def test_adapter_attaches_preset_when_enabled(monkeypatch):
    monkeypatch.setenv("BITGET_NATIVE_TPSL", "1")
    a = _adapter()
    await a.place_order(_req())
    assert a._client.kw["preset_tp_price"] == Decimal("99")
    assert a._client.kw["preset_sl_price"] == Decimal("100.5")


@pytest.mark.asyncio
async def test_adapter_no_preset_when_disabled(monkeypatch):
    monkeypatch.setenv("BITGET_NATIVE_TPSL", "0")
    a = _adapter()
    await a.place_order(_req())
    assert a._client.kw["preset_tp_price"] is None
    assert a._client.kw["preset_sl_price"] is None


@pytest.mark.asyncio
async def test_adapter_no_preset_on_reduce_only(monkeypatch):
    monkeypatch.setenv("BITGET_NATIVE_TPSL", "1")
    a = _adapter()
    await a.place_order(_req(reduce_only=True))  # 청산엔 preset 안 붙임
    assert a._client.kw["preset_tp_price"] is None
    assert a._client.kw["preset_sl_price"] is None
