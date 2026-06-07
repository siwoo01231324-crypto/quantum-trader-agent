"""거래소 네이티브 TP/SL plan order (2026-06-08) 단위테스트.

핵심 검증:
  - planType 매핑: STOP_MARKET→loss_plan, TAKE_PROFIT_MARKET→profit_plan
  - holdSide 매핑: close_side BUY(숏 청산)→short, SELL(롱 청산)→long
  - trigger price = *코인가격* pct (ROI 아님). 숏 진입 E → SL=E×1.005(-5%ROI),
    TP=E×0.99(+10%ROI) @10x.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.brokers.bitget.async_adapter import AsyncBitgetFuturesAdapter
from src.brokers.protective_orders import (
    ProtectiveOrderConfig,
    ProtectiveOrderManager,
)


class _MockClient:
    def __init__(self):
        self.calls = []
        self.cancelled = []

    async def place_tpsl_order(self, **kw):
        self.calls.append(kw)
        return f"oid-{kw['plan_type']}"

    async def cancel_tpsl_order(self, **kw):
        self.cancelled.append(kw)

    async def get_pending_tpsl_orders(self, **kw):
        return [{"orderId": "x", "symbol": kw.get("symbol")}]


class _IdFilters:
    def quantize_price(self, symbol, price):
        return price


def _adapter() -> AsyncBitgetFuturesAdapter:
    a = AsyncBitgetFuturesAdapter.__new__(AsyncBitgetFuturesAdapter)
    a._client = _MockClient()
    a._symbol_filters = _IdFilters()
    return a


@pytest.mark.asyncio
async def test_short_sl_maps_loss_plan_short_holdside():
    a = _adapter()
    oid = await a.place_protective_order(
        symbol="BTCUSDT", side="BUY", qty=Decimal("3"),
        stop_price=Decimal("100.5"), kind="STOP_MARKET",
    )
    call = a._client.calls[0]
    assert call["plan_type"] == "loss_plan"
    assert call["hold_side"] == "short"        # BUY 청산 = 숏 포지션
    assert call["trigger_price"] == Decimal("100.5")
    assert call["size"] == Decimal("3")
    assert oid == "oid-loss_plan"


@pytest.mark.asyncio
async def test_short_tp_maps_profit_plan():
    a = _adapter()
    await a.place_protective_order(
        symbol="BTCUSDT", side="BUY", qty=Decimal("3"),
        stop_price=Decimal("99.0"), kind="TAKE_PROFIT_MARKET",
    )
    call = a._client.calls[0]
    assert call["plan_type"] == "profit_plan"
    assert call["hold_side"] == "short"


@pytest.mark.asyncio
async def test_long_close_maps_long_holdside():
    a = _adapter()
    await a.place_protective_order(
        symbol="BTCUSDT", side="SELL", qty=Decimal("3"),
        stop_price=Decimal("99.5"), kind="STOP_MARKET",
    )
    assert a._client.calls[0]["hold_side"] == "long"  # SELL 청산 = 롱 포지션


def test_roi_vs_price_short_trigger_prices():
    """숏 진입 E=100, 가격pct SL 0.005 / TP 0.01 → SL=100.5, TP=99.0.

    10x 에서 SL 100.5 = 가격+0.5% = ROI -5%, TP 99.0 = 가격-1% = ROI +10%.
    ROI 숫자(0.05/0.10)를 직접 쓰면 SL 105/TP 90 = 10배 어긋남 — 회귀 가드.
    """
    cfg = ProtectiveOrderConfig(
        stop_loss_pct=Decimal("0.005"), take_profit_pct=Decimal("0.01"),
    )
    sl, tp, close_side = ProtectiveOrderManager._compute_protection_prices(
        entry_side="SELL", entry_price=Decimal("100"), config=cfg,
    )
    assert sl == Decimal("100.500")   # 숏 손절 = 가격 위 (-5% ROI @10x)
    assert tp == Decimal("99.00")     # 숏 익절 = 가격 아래 (+10% ROI @10x)
    assert close_side == "BUY"


def test_roi_vs_price_long_trigger_prices():
    cfg = ProtectiveOrderConfig(
        stop_loss_pct=Decimal("0.005"), take_profit_pct=Decimal("0.01"),
    )
    sl, tp, close_side = ProtectiveOrderManager._compute_protection_prices(
        entry_side="BUY", entry_price=Decimal("100"), config=cfg,
    )
    assert sl == Decimal("99.500")    # 롱 손절 = 가격 아래
    assert tp == Decimal("101.00")    # 롱 익절 = 가격 위
    assert close_side == "SELL"


@pytest.mark.asyncio
async def test_cancel_and_list_protective():
    a = _adapter()
    await a.cancel_protective_order(symbol="BTCUSDT", broker_order_id="oid-1")
    assert a._client.cancelled[0]["order_id"] == "oid-1"
    rows = await a.list_open_protective_orders(symbol="BTCUSDT")
    assert rows and rows[0]["symbol"] == "BTCUSDT"
