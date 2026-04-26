from __future__ import annotations
from decimal import Decimal
import pytest

from src.live.conversion import intent_to_order_request, SYMBOL_STEP_SIZES
from src.brokers.base import OrderType
from src.execution.base import Side, TimeInForce
from src.portfolio.order_intent import OrderIntent


def _intent(symbol: str, side: str, qty: float) -> OrderIntent:
    return OrderIntent(strategy_id="s", symbol=symbol, side=side, qty=qty, reason="r")


def test_btcusdt_truncate():
    req = intent_to_order_request(_intent("BTCUSDT", "buy", 0.0099), idempotency_key="k1")
    assert req.qty == Decimal("0.009")


def test_btcusdt_exact():
    req = intent_to_order_request(_intent("BTCUSDT", "buy", 0.001), idempotency_key="k2")
    assert req.qty == Decimal("0.001")


def test_one_third_truncate():
    req = intent_to_order_request(_intent("BTCUSDT", "buy", 1 / 3), idempotency_key="k3")
    assert req.qty == Decimal("0.333")


def test_ethusdt():
    req = intent_to_order_request(_intent("ETHUSDT", "buy", 0.5), idempotency_key="k4")
    assert req.qty == Decimal("0.500")


def test_solusdt_step1():
    req = intent_to_order_request(_intent("SOLUSDT", "buy", 10.7), idempotency_key="k5")
    assert req.qty == Decimal("10")


def test_unknown_symbol_raises():
    with pytest.raises(ValueError, match="Unsupported symbol"):
        intent_to_order_request(_intent("DOGEUSDT", "buy", 1.0), idempotency_key="k6")


def test_side_mapping():
    buy_req = intent_to_order_request(_intent("BTCUSDT", "buy", 0.001), idempotency_key="k7")
    sell_req = intent_to_order_request(_intent("BTCUSDT", "sell", 0.001), idempotency_key="k8")
    assert buy_req.side == Side.BUY
    assert sell_req.side == Side.SELL


def test_idempotency_key_propagated():
    req = intent_to_order_request(_intent("BTCUSDT", "buy", 0.001), idempotency_key="my-key-123")
    assert req.client_order_id == "my-key-123"


def test_default_market_order():
    req = intent_to_order_request(_intent("BTCUSDT", "buy", 0.001), idempotency_key="k9")
    assert req.order_type == OrderType.MARKET
    assert req.tif == TimeInForce.GTC
    assert req.price is None
