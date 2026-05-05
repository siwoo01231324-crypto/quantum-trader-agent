"""Tests for BinanceFuturesAdapter.place_protective_order / cancel / list (#127).

Validates that the adapter correctly translates protective-order requests into
Binance Futures REST payloads (STOP_MARKET / TAKE_PROFIT_MARKET reduceOnly).
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.brokers.binance.adapter import BinanceFuturesAdapter


@pytest.fixture
def adapter():
    """Adapter with mocked underlying client + symbol filters bypass."""
    inst = BinanceFuturesAdapter.__new__(BinanceFuturesAdapter)
    inst.paper = True
    inst._kill_switch = None
    inst._hedge_mode = False
    client = MagicMock()
    client._now_ms = MagicMock(return_value=1717000000000)
    client._post = MagicMock(return_value={"orderId": 99999, "clientOrderId": "x"})
    client._get = MagicMock(return_value=[])
    client.cancel_order = MagicMock()
    inst._client = client
    inst._symbol_filters = MagicMock()
    return inst


class TestPlaceProtective:
    def test_stop_market_payload(self, adapter):
        order_id = adapter.place_protective_order(
            symbol="BTCUSDT",
            side="SELL",
            qty=Decimal("0.1"),
            stop_price=Decimal("49000"),
            kind="STOP_MARKET",
        )
        assert order_id == "99999"
        adapter._client._post.assert_called_once()
        call_args = adapter._client._post.call_args
        assert call_args[0][0] == "/fapi/v1/order"
        params = call_args[0][1]
        assert params["symbol"] == "BTCUSDT"
        assert params["side"] == "SELL"
        assert params["type"] == "STOP_MARKET"
        assert params["quantity"] == "0.1"
        assert params["stopPrice"] == "49000"
        assert params["reduceOnly"] == "true"
        assert params["timeInForce"] == "GTC"
        assert params["workingType"] == "MARK_PRICE"
        assert params["newClientOrderId"]  # non-empty

    def test_take_profit_market_payload(self, adapter):
        order_id = adapter.place_protective_order(
            symbol="BTCUSDT",
            side="SELL",
            qty=Decimal("0.1"),
            stop_price=Decimal("52000"),
            kind="TAKE_PROFIT_MARKET",
        )
        assert order_id == "99999"
        params = adapter._client._post.call_args[0][1]
        assert params["type"] == "TAKE_PROFIT_MARKET"
        assert params["stopPrice"] == "52000"

    def test_invalid_kind_rejected(self, adapter):
        with pytest.raises(ValueError, match="unsupported protective kind"):
            adapter.place_protective_order(
                symbol="BTCUSDT", side="SELL", qty=Decimal("0.1"),
                stop_price=Decimal("49000"), kind="LIMIT",
            )

    def test_invalid_side_rejected(self, adapter):
        with pytest.raises(ValueError, match="side must be"):
            adapter.place_protective_order(
                symbol="BTCUSDT", side="HOLD", qty=Decimal("0.1"),
                stop_price=Decimal("49000"), kind="STOP_MARKET",
            )

    def test_missing_orderid_in_response_raises(self, adapter):
        adapter._client._post = MagicMock(return_value={"clientOrderId": "x"})
        with pytest.raises(RuntimeError, match="missing orderId"):
            adapter.place_protective_order(
                symbol="BTCUSDT", side="SELL", qty=Decimal("0.1"),
                stop_price=Decimal("49000"), kind="STOP_MARKET",
            )


class TestCancelProtective:
    def test_cancel_calls_client(self, adapter):
        adapter.cancel_protective_order(symbol="BTCUSDT", broker_order_id="123")
        adapter._client.cancel_order.assert_called_once_with(
            "BTCUSDT", broker_order_id="123",
        )


class TestListOpenProtective:
    def test_list_filters_by_type(self, adapter):
        adapter._client._get = MagicMock(return_value=[
            {"orderId": 1, "symbol": "BTCUSDT", "type": "LIMIT", "side": "BUY"},
            {"orderId": 2, "symbol": "BTCUSDT", "type": "STOP_MARKET", "side": "SELL",
             "stopPrice": "49000", "clientOrderId": "p1"},
            {"orderId": 3, "symbol": "BTCUSDT", "type": "TAKE_PROFIT_MARKET", "side": "SELL",
             "stopPrice": "52000", "clientOrderId": "p2"},
        ])
        protective = adapter.list_open_protective_orders(symbol="BTCUSDT")
        assert len(protective) == 2
        types = {p["type"] for p in protective}
        assert types == {"STOP_MARKET", "TAKE_PROFIT_MARKET"}

    def test_list_empty(self, adapter):
        adapter._client._get = MagicMock(return_value=[])
        assert adapter.list_open_protective_orders() == []

    def test_list_non_list_response_returns_empty(self, adapter):
        # Some Binance error responses return a dict — should not crash.
        adapter._client._get = MagicMock(return_value={"code": -1000, "msg": "error"})
        assert adapter.list_open_protective_orders() == []
