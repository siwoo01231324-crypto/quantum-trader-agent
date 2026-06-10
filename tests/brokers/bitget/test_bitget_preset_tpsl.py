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
    a._native_tpsl_symbols = set()  # P2 — has_native_tpsl 추적
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


# ── preset 가격 거부(40836) → preset 없이 재시도 (진입 보존) ─────────────────


@pytest.mark.asyncio
async def test_adapter_retries_without_preset_on_40836(monkeypatch):
    """진입가 대비 시장이 움직여 preset SL 이 즉시 트리거 조건 → 40836 으로
    주문 전체가 거부될 때, preset 없이 1회 재시도해 *진입을 보존* (synthetic 백업).
    """
    monkeypatch.setenv("BITGET_NATIVE_TPSL", "1")
    from src.brokers.errors import UnknownError  # 40836 은 map 에 없으면 UnknownError
    from src.brokers.bitget.schemas import PlaceOrderResponse

    class _RejectThenAccept:
        def __init__(self):
            self.calls = []

        async def place_order(self, **kw):
            self.calls.append(kw)
            if len(self.calls) == 1:
                raise UnknownError(
                    "[40836] The stop loss price of the short position "
                    "should be greater than the current price"
                )
            return PlaceOrderResponse.from_json({"orderId": "9", "clientOid": "c"})

    a = _adapter()
    a._client = _RejectThenAccept()
    ack = await a.place_order(_req())

    assert len(a._client.calls) == 2  # 1차 preset 부착, 2차 preset 제거
    assert a._client.calls[0]["preset_sl_price"] == Decimal("100.5")
    assert a._client.calls[1]["preset_tp_price"] is None
    assert a._client.calls[1]["preset_sl_price"] is None
    assert ack.broker_order_id == "9"  # 진입 성공


@pytest.mark.asyncio
async def test_adapter_non_preset_error_propagates(monkeypatch):
    """preset 무관 에러(잔고부족)는 재시도 없이 그대로 전파."""
    monkeypatch.setenv("BITGET_NATIVE_TPSL", "1")
    from src.brokers.errors import InsufficientFundsError

    class _Reject:
        def __init__(self):
            self.calls = 0

        async def place_order(self, **kw):
            self.calls += 1
            raise InsufficientFundsError("[43012] insufficient balance")

    a = _adapter()
    a._client = _Reject()
    with pytest.raises(InsufficientFundsError):
        await a.place_order(_req())
    assert a._client.calls == 1  # 재시도 없음


# ── P2: native TP/SL 활성 종목 추적 (synthetic stand-down 용) ─────────────────


@pytest.mark.asyncio
async def test_adapter_tracks_native_tpsl_on_preset_entry(monkeypatch):
    """preset 부착 진입 성공 → has_native_tpsl True (synthetic 손 뗌)."""
    monkeypatch.setenv("BITGET_NATIVE_TPSL", "1")
    a = _adapter()
    await a.place_order(_req())
    assert a.has_native_tpsl("BTCUSDT") is True


@pytest.mark.asyncio
async def test_adapter_native_tpsl_false_on_naked_retry(monkeypatch):
    """40836 → preset 없이 재시도(naked) → has_native_tpsl False (synthetic 백업)."""
    monkeypatch.setenv("BITGET_NATIVE_TPSL", "1")
    from src.brokers.errors import UnknownError
    from src.brokers.bitget.schemas import PlaceOrderResponse

    class _RejectThenAccept:
        def __init__(self):
            self.calls = []

        async def place_order(self, **kw):
            self.calls.append(kw)
            if len(self.calls) == 1:
                raise UnknownError(
                    "[40836] The stop loss price of the short position "
                    "should be greater than the current price"
                )
            return PlaceOrderResponse.from_json({"orderId": "9", "clientOid": "c"})

    a = _adapter()
    a._client = _RejectThenAccept()
    await a.place_order(_req())
    assert a.has_native_tpsl("BTCUSDT") is False


@pytest.mark.asyncio
async def test_adapter_native_tpsl_discarded_on_reduce_only(monkeypatch):
    """청산(reduce_only) → 추적 해제 (포지션 없어짐)."""
    monkeypatch.setenv("BITGET_NATIVE_TPSL", "1")
    a = _adapter()
    await a.place_order(_req())
    assert a.has_native_tpsl("BTCUSDT") is True
    await a.place_order(_req(reduce_only=True))
    assert a.has_native_tpsl("BTCUSDT") is False


@pytest.mark.asyncio
async def test_adapter_native_tpsl_false_when_gate_off(monkeypatch):
    """BITGET_NATIVE_TPSL=0 → preset 미부착 → has_native_tpsl False."""
    monkeypatch.setenv("BITGET_NATIVE_TPSL", "0")
    a = _adapter()
    await a.place_order(_req())
    assert a.has_native_tpsl("BTCUSDT") is False
