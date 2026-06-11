"""거래소 네이티브 TP/SL plan order (2026-06-08) 단위테스트.

핵심 검증:
  - planType 매핑: STOP_MARKET→loss_plan, TAKE_PROFIT_MARKET→profit_plan
  - holdSide 매핑 (2026-06-11 데모 실측): **one-way 모드(default)는 buy/sell**,
    hedge 모드는 long/short. close_side BUY(숏 청산)→one-way "sell"/hedge "short",
    SELL(롱 청산)→one-way "buy"/hedge "long". ("short"/"long" 을 one-way 에 보내면
    [43011] holdSide error — naked SL 미체결 사고의 정체.)
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
async def test_short_sl_oneway_maps_loss_plan_sell_holdside():
    # one-way(default, _hedge_mode 미설정) → 숏 보호 holdSide="sell" (데모 실측).
    a = _adapter()
    oid = await a.place_protective_order(
        symbol="BTCUSDT", side="BUY", qty=Decimal("3"),
        stop_price=Decimal("100.5"), kind="STOP_MARKET",
    )
    call = a._client.calls[0]
    assert call["plan_type"] == "pos_loss"     # whole-position SL (2026-06-12)
    assert call["hold_side"] == "sell"         # one-way: BUY 청산 = 숏 → "sell"
    assert call["trigger_price"] == Decimal("100.5")
    assert call.get("size") is None            # whole-position = size 생략
    assert oid == "oid-pos_loss"


@pytest.mark.asyncio
async def test_short_tp_oneway_maps_profit_plan_sell():
    a = _adapter()
    await a.place_protective_order(
        symbol="BTCUSDT", side="BUY", qty=Decimal("3"),
        stop_price=Decimal("99.0"), kind="TAKE_PROFIT_MARKET",
    )
    call = a._client.calls[0]
    assert call["plan_type"] == "pos_profit"   # whole-position TP (2026-06-12)
    assert call["hold_side"] == "sell"


@pytest.mark.asyncio
async def test_long_close_oneway_maps_buy_holdside():
    a = _adapter()
    await a.place_protective_order(
        symbol="BTCUSDT", side="SELL", qty=Decimal("3"),
        stop_price=Decimal("99.5"), kind="STOP_MARKET",
    )
    assert a._client.calls[0]["hold_side"] == "buy"   # one-way: SELL 청산 = 롱 → "buy"


@pytest.mark.asyncio
async def test_hedge_mode_maps_long_short_holdside():
    # hedge 모드(_hedge_mode=True) → "short"/"long" 유지 (회귀 가드).
    a = _adapter()
    a._hedge_mode = True
    await a.place_protective_order(
        symbol="BTCUSDT", side="BUY", qty=Decimal("3"),
        stop_price=Decimal("100.5"), kind="STOP_MARKET",
    )
    assert a._client.calls[0]["hold_side"] == "short"  # hedge: BUY 청산 = 숏
    a2 = _adapter()
    a2._hedge_mode = True
    await a2.place_protective_order(
        symbol="BTCUSDT", side="SELL", qty=Decimal("3"),
        stop_price=Decimal("99.5"), kind="STOP_MARKET",
    )
    assert a2._client.calls[0]["hold_side"] == "long"  # hedge: SELL 청산 = 롱


@pytest.mark.asyncio
async def test_protective_43023_settle_retry_then_succeeds(monkeypatch):
    # 43023(포지션 미정착) 2회 후 성공 — backoff 재시도 검증 (2026-06-11 라이브).
    import src.brokers.bitget.async_adapter as mod
    sleeps: list[float] = []
    async def _fake_sleep(d):
        sleeps.append(d)
    monkeypatch.setattr(mod.asyncio, "sleep", _fake_sleep)
    a = _adapter()
    n = {"c": 0}
    async def _flaky(**kw):
        n["c"] += 1
        if n["c"] <= 2:
            raise RuntimeError(
                "[43023] Insufficient position, can not set profit or stop loss"
            )
        return f"oid-{kw['plan_type']}"
    a._client.place_tpsl_order = _flaky
    oid = await a.place_protective_order(
        symbol="BTCUSDT", side="BUY", qty=Decimal("3"),
        stop_price=Decimal("100.5"), kind="STOP_MARKET",
    )
    assert oid == "oid-pos_loss"
    assert n["c"] == 3           # 2 실패 + 1 성공
    assert len(sleeps) == 2      # 성공 전 2회 backoff


@pytest.mark.asyncio
async def test_protective_non_43023_raises_immediately(monkeypatch):
    # 43023 외 에러(40836 등)는 재시도 없이 즉시 raise.
    import src.brokers.bitget.async_adapter as mod
    async def _fake_sleep(d):
        pass
    monkeypatch.setattr(mod.asyncio, "sleep", _fake_sleep)
    a = _adapter()
    n = {"c": 0}
    async def _bad(**kw):
        n["c"] += 1
        raise RuntimeError("[40836] preset price error")
    a._client.place_tpsl_order = _bad
    with pytest.raises(RuntimeError):
        await a.place_protective_order(
            symbol="BTCUSDT", side="BUY", qty=Decimal("3"),
            stop_price=Decimal("100.5"), kind="STOP_MARKET",
        )
    assert n["c"] == 1           # 재시도 안 함


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
