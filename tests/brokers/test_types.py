from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

import pytest

from src.brokers.types import BrokerFill


def _fill(**kwargs) -> BrokerFill:
    defaults = dict(
        parent_id="p1",
        broker_order_id="b1",
        client_order_id="c1",
        trade_id="t1",
        qty=Decimal("1.5"),
        price=Decimal("50000.10"),
        fee=Decimal("0.001"),
        fee_asset="USDT",
        ts=datetime(2026, 4, 20, 9, 0),
        is_maker=False,
    )
    defaults.update(kwargs)
    return BrokerFill(**defaults)


def test_broker_fill_accepts_decimal():
    f = _fill(qty=Decimal("0.001"), price=Decimal("99999.99"))
    assert f.qty == Decimal("0.001")
    assert f.price == Decimal("99999.99")


def test_broker_fill_decimal_precision_preserved():
    f = _fill(qty=Decimal("1.23456789"), price=Decimal("0.00000001"))
    assert f.qty == Decimal("1.23456789")
    assert f.price == Decimal("0.00000001")
    # Decimal value equality holds regardless of string representation (1E-8 == 0.00000001)
    assert f.price == Decimal("1E-8")


def test_broker_fill_rejects_float_qty():
    with pytest.raises((TypeError, ValueError)):
        _fill(qty=1.5)  # type: ignore[arg-type]


def test_broker_fill_rejects_float_price():
    with pytest.raises((TypeError, ValueError)):
        _fill(price=50000.10)  # type: ignore[arg-type]


def test_broker_fill_rejects_float_fee():
    with pytest.raises((TypeError, ValueError)):
        _fill(fee=0.001)  # type: ignore[arg-type]


def test_broker_fill_zero_fee_allowed():
    f = _fill(fee=Decimal("0"))
    assert f.fee == Decimal("0")


def test_broker_fill_is_frozen():
    f = _fill()
    with pytest.raises((AttributeError, TypeError)):
        f.qty = Decimal("999")  # type: ignore[misc]


def test_broker_fill_fee_asset_krw():
    f = _fill(fee_asset="KRW", fee=Decimal("10"))
    assert f.fee_asset == "KRW"


def test_broker_fill_maker_flag():
    f = _fill(is_maker=True)
    assert f.is_maker is True


def test_broker_fill_trade_id_uniqueness_fields():
    f = _fill(broker_order_id="bo1", trade_id="t99")
    assert f.broker_order_id == "bo1"
    assert f.trade_id == "t99"
