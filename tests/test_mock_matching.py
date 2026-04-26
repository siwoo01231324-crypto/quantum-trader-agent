from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.brokers.base import OrderRequest, OrderType
from src.brokers.types import BrokerFill
from src.execution.base import MarketState, Side, Tick, TimeInForce
from src.execution.mock_matching import MockMatchingEngine


def _tick(bid: float = 99.0, ask: float = 101.0, last: float = 100.0) -> Tick:
    return Tick(symbol="BTCUSDT", bid=bid, ask=ask, last=last, volume=1000, ts=datetime.now(timezone.utc))


def _market_state(**kwargs) -> MarketState:
    return MarketState(tick=_tick(**kwargs))


def _order(
    side: Side,
    qty: str = "1",
    order_type: OrderType = OrderType.MARKET,
    price: str | None = None,
) -> OrderRequest:
    return OrderRequest(
        client_order_id="cid-001",
        symbol="BTCUSDT",
        side=side,
        qty=Decimal(qty),
        order_type=order_type,
        price=Decimal(price) if price is not None else None,
        tif=TimeInForce.GTC,
    )


# ---------------------------------------------------------------------------
# market orders
# ---------------------------------------------------------------------------

def test_market_buy_immediate_fill():
    engine = MockMatchingEngine()
    fills = engine.match(_order(Side.BUY), _market_state(last=100.0))
    assert len(fills) == 1
    fill = fills[0]
    assert fill.price == Decimal("100")
    assert fill.qty == Decimal("1")


def test_market_sell_immediate_fill():
    engine = MockMatchingEngine()
    fills = engine.match(_order(Side.SELL), _market_state(last=100.0))
    assert len(fills) == 1
    fill = fills[0]
    assert fill.price == Decimal("100")
    assert fill.qty == Decimal("1")


# ---------------------------------------------------------------------------
# limit orders
# ---------------------------------------------------------------------------

def test_limit_buy_fillable():
    # price >= ask → fills
    engine = MockMatchingEngine()
    fills = engine.match(
        _order(Side.BUY, order_type=OrderType.LIMIT, price="101"),
        _market_state(bid=99.0, ask=101.0, last=100.0),
    )
    assert len(fills) == 1


def test_limit_buy_unfillable():
    # price < ask → no fill
    engine = MockMatchingEngine()
    fills = engine.match(
        _order(Side.BUY, order_type=OrderType.LIMIT, price="100"),
        _market_state(bid=99.0, ask=101.0, last=100.0),
    )
    assert fills == []


def test_limit_sell_fillable():
    # price <= bid → fills
    engine = MockMatchingEngine()
    fills = engine.match(
        _order(Side.SELL, order_type=OrderType.LIMIT, price="99"),
        _market_state(bid=99.0, ask=101.0, last=100.0),
    )
    assert len(fills) == 1


def test_limit_sell_unfillable():
    # price > bid → no fill
    engine = MockMatchingEngine()
    fills = engine.match(
        _order(Side.SELL, order_type=OrderType.LIMIT, price="100"),
        _market_state(bid=99.0, ask=101.0, last=100.0),
    )
    assert fills == []


# ---------------------------------------------------------------------------
# fee calculation
# ---------------------------------------------------------------------------

def test_taker_fee_calculation():
    # 1 BTC at 50000 USDT, taker 0.05% → fee = 25 USDT
    engine = MockMatchingEngine()
    fills = engine.match(
        _order(Side.BUY, qty="1"),
        _market_state(bid=49999.0, ask=50001.0, last=50000.0),
    )
    assert len(fills) == 1
    assert fills[0].fee == Decimal("25.00000000")


# ---------------------------------------------------------------------------
# Decimal safety
# ---------------------------------------------------------------------------

def test_decimal_precision_no_float():
    engine = MockMatchingEngine()
    fills = engine.match(_order(Side.BUY), _market_state())
    fill = fills[0]
    assert isinstance(fill.qty, Decimal)
    assert isinstance(fill.price, Decimal)
    assert isinstance(fill.fee, Decimal)
    # BrokerFill.__post_init__ raises TypeError for float — double-check
    with pytest.raises(TypeError):
        BrokerFill(
            parent_id="p",
            broker_order_id="b",
            client_order_id="c",
            trade_id="0",
            qty=1.0,  # float — must be rejected
            price=Decimal("100"),
            fee=Decimal("0"),
            fee_asset="USDT",
            ts=datetime.now(timezone.utc),
            is_maker=False,
        )


# ---------------------------------------------------------------------------
# trade_id auto-increment
# ---------------------------------------------------------------------------

def test_trade_id_increments():
    engine = MockMatchingEngine()
    order = _order(Side.BUY)
    state = _market_state()
    fills = [engine.match(order, state) for _ in range(3)]
    trade_ids = [f[0].trade_id for f in fills]
    assert trade_ids == ["0", "1", "2"]


# ---------------------------------------------------------------------------
# fee_asset default
# ---------------------------------------------------------------------------

def test_fee_asset_default_usdt():
    engine = MockMatchingEngine()
    fills = engine.match(_order(Side.BUY), _market_state())
    assert fills[0].fee_asset == "USDT"


# ---------------------------------------------------------------------------
# partial fill disabled (Phase 1)
# ---------------------------------------------------------------------------

def test_partial_fill_disabled_phase1():
    engine = MockMatchingEngine()
    assert engine.partial_fill_enabled is False
    order = _order(Side.BUY, qty="5")
    fills = engine.match(order, _market_state())
    assert len(fills) == 1
    assert fills[0].qty == Decimal("5")
