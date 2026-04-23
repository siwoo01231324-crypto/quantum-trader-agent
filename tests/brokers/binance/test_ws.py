"""Tests for Binance WS user data stream and ReconnectReconciler.

Uses in-process fakes — zero real network traffic.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, call, patch

import pytest
import responses as rsps_lib

from src.brokers.binance.reconciler import ReconnectReconciler
from src.brokers.binance.rest import BinanceFuturesClient
from src.brokers.binance.ws import BinanceUserDataStream
from src.brokers.rate_limiter import RateLimiter
from src.brokers.types import BrokerFill

BASE_URL = "https://testnet.binancefuture.com"
WS_BASE_URL = "wss://fstream.binancefuture.com/ws"
API_KEY = "test-key"
SECRET = "test-secret"


def _make_client() -> BinanceFuturesClient:
    rl = RateLimiter()
    rl.register_bucket("weight", rate=100.0, capacity=6000.0)
    rl.register_bucket("orders_1m", rate=20.0, capacity=1200.0)
    rl.register_bucket("orders_10s", rate=30.0, capacity=300.0)
    client = BinanceFuturesClient(
        api_key=API_KEY,
        secret=SECRET,
        base_url=BASE_URL,
        rate_limiter=rl,
    )
    client._last_sync = time.monotonic()  # skip auto-sync
    return client


def _trade_event(
    broker_order_id: str = "1001",
    trade_id: str = "5001",
    client_order_id: str = "cid-1",
    symbol: str = "BTCUSDT",
    qty: str = "0.001",
    price: str = "50000",
    fee: str = "0.05",
    fee_asset: str = "USDT",
    ts_ms: int = 1700000000000,
    is_maker: bool = False,
) -> dict:
    return {
        "x": "TRADE",
        "i": broker_order_id,
        "t": trade_id,
        "c": client_order_id,
        "s": symbol,
        "l": qty,
        "L": price,
        "n": fee,
        "N": fee_asset,
        "T": ts_ms,
        "m": is_maker,
    }


# ── ReconnectReconciler tests ─────────────────────────────────────────────────


class TestReconnectReconciler:
    def _make_reconciler(self) -> tuple[ReconnectReconciler, list[BrokerFill]]:
        client = _make_client()
        received: list[BrokerFill] = []
        rec = ReconnectReconciler(client=client, on_fill=received.append)
        return rec, received

    def test_order_trade_update_dispatches_fill(self):
        rec, received = self._make_reconciler()
        o = _trade_event()
        fill = rec.on_trade_event(o)
        assert fill is not None
        assert fill.broker_order_id == "1001"
        assert fill.trade_id == "5001"
        assert fill.qty == Decimal("0.001")
        assert fill.price == Decimal("50000")
        assert fill.fee == Decimal("0.05")
        assert fill.fee_asset == "USDT"
        assert fill.is_maker is False

    def test_duplicate_fill_is_skipped(self):
        rec, received = self._make_reconciler()
        o = _trade_event(broker_order_id="1001", trade_id="5001")
        fill1 = rec.on_trade_event(o)
        fill2 = rec.on_trade_event(o)  # same (broker_order_id, trade_id)
        assert fill1 is not None
        assert fill2 is None  # deduplicated

    def test_different_trade_ids_not_deduplicated(self):
        rec, received = self._make_reconciler()
        o1 = _trade_event(trade_id="5001")
        o2 = _trade_event(trade_id="5002")
        f1 = rec.on_trade_event(o1)
        f2 = rec.on_trade_event(o2)
        assert f1 is not None
        assert f2 is not None

    @rsps_lib.activate
    def test_reconcile_calls_open_orders(self):
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/openOrders",
            json=[],
            status=200,
        )
        client = _make_client()
        rec = ReconnectReconciler(
            client=client,
            on_fill=lambda f: None,
            tracked_symbols=["BTCUSDT"],
        )
        rec.reconcile()
        assert len(rsps_lib.calls) == 1

    @rsps_lib.activate
    def test_reconcile_survives_rest_error(self):
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/openOrders",
            body=Exception("network error"),
        )
        client = _make_client()
        rec = ReconnectReconciler(client=client, on_fill=lambda f: None)
        # Should not raise — error is logged
        rec.reconcile()


# ── Partial fill sequence tests ───────────────────────────────────────────────


class TestPartialFills:
    """50% → 75% → 90% → 100% fill sequence."""

    def test_partial_fill_sequence(self):
        client = _make_client()
        received: list[BrokerFill] = []
        rec = ReconnectReconciler(client=client, on_fill=received.append)

        total_qty = Decimal("0.004")
        price = Decimal("50000")
        fills = [
            ("t001", "0.002"),   # 50%
            ("t002", "0.001"),   # 75%
            ("t003", "0.0004"),  # 90%
            ("t004", "0.0006"),  # 100%
        ]

        for trade_id, qty in fills:
            o = _trade_event(
                broker_order_id="order-1",
                trade_id=trade_id,
                qty=qty,
                price=str(price),
            )
            fill = rec.on_trade_event(o)
            assert fill is not None
            received.append(fill)

        assert len(received) == 4
        cumulative_qty = sum(f.qty for f in received)
        assert cumulative_qty == total_qty

    def test_partial_fill_cumulative_fee(self):
        client = _make_client()
        received: list[BrokerFill] = []
        rec = ReconnectReconciler(client=client, on_fill=received.append)

        fees = ["0.01", "0.02", "0.015", "0.005"]
        for i, fee in enumerate(fees):
            o = _trade_event(trade_id=str(1000 + i), fee=fee)
            fill = rec.on_trade_event(o)
            assert fill is not None
            received.append(fill)

        total_fee = sum(f.fee for f in received)
        assert total_fee == Decimal("0.05")


# ── WS stream lifecycle tests ─────────────────────────────────────────────────


class TestListenKeyLifecycle:
    @rsps_lib.activate
    def test_listen_key_issued_on_start(self):
        rsps_lib.add(
            rsps_lib.POST,
            f"{BASE_URL}/fapi/v1/listenKey",
            json={"listenKey": "test-listen-key-abc"},
            status=200,
        )

        client = _make_client()
        rec = ReconnectReconciler(client=client, on_fill=lambda f: None)

        with patch("websocket.WebSocketApp") as mock_ws_cls:
            mock_ws = MagicMock()
            mock_ws_cls.return_value = mock_ws
            mock_ws.run_forever = lambda: None

            stream = BinanceUserDataStream(
                client=client,
                ws_base_url=WS_BASE_URL,
                on_fill=lambda f: None,
                reconciler=rec,
            )
            stream.start()
            stream.close()

        assert stream._listen_key == "test-listen-key-abc"
        assert len(rsps_lib.calls) >= 1

    def test_non_trade_events_not_dispatched(self):
        client = _make_client()
        received: list[BrokerFill] = []
        rec = ReconnectReconciler(client=client, on_fill=received.append)

        stream = BinanceUserDataStream(
            client=client,
            ws_base_url=WS_BASE_URL,
            on_fill=received.append,
            reconciler=rec,
        )

        # Simulate WS message with exec_type=NEW (not TRADE)
        msg = {
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "x": "NEW",  # not TRADE
                "i": "1001",
                "t": "0",
                "s": "BTCUSDT",
            },
        }
        import json
        stream._on_message(None, json.dumps(msg))
        assert len(received) == 0

    def test_trade_event_dispatched_to_on_fill(self):
        client = _make_client()
        received: list[BrokerFill] = []
        rec = ReconnectReconciler(client=client, on_fill=received.append)

        stream = BinanceUserDataStream(
            client=client,
            ws_base_url=WS_BASE_URL,
            on_fill=received.append,
            reconciler=rec,
        )

        msg = {
            "e": "ORDER_TRADE_UPDATE",
            "o": {
                "x": "TRADE",
                **_trade_event(),
            },
        }
        import json
        stream._on_message(None, json.dumps(msg))
        assert len(received) == 1
        assert received[0].broker_order_id == "1001"


class TestRestAckBeforeWsNew:
    """C4: REST ACK arrives before WS NEW — REST is source of truth."""

    def test_rest_ack_before_ws_new_is_source_of_truth(self):
        """The adapter uses REST response for the initial OrderAck; WS TRADE
        events add fills on top. If WS NEW arrives after REST ACK, the
        order is already known — no double-counting."""
        client = _make_client()
        received: list[BrokerFill] = []
        rec = ReconnectReconciler(client=client, on_fill=received.append)

        # Simulate WS NEW event (exec_type=NEW — should be ignored by reconciler)
        new_event = {
            "x": "NEW",
            "i": "2000",
            "t": "-1",
            "s": "BTCUSDT",
        }
        result = rec.on_trade_event(new_event)
        assert result is None  # NEW exec_type → no fill

        # Then TRADE event arrives — this is the fill
        trade_event = _trade_event(broker_order_id="2000", trade_id="9001")
        fill = rec.on_trade_event(trade_event)
        assert fill is not None
        assert fill.broker_order_id == "2000"
        assert len(received) == 0  # on_fill not called via reconciler directly


class TestReconnectReconciliation:
    """C4: After reconnect, get_open_orders is polled (reconcile)."""

    @rsps_lib.activate
    def test_reconnect_reconciles_open_orders(self):
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/openOrders",
            json=[
                {
                    "orderId": 3001,
                    "clientOrderId": "strategy-cid",
                    "symbol": "BTCUSDT",
                    "status": "NEW",
                    "origQty": "0.001",
                    "price": "50000",
                    "avgPrice": "0",
                    "updateTime": 1700000000000,
                }
            ],
            status=200,
        )

        client = _make_client()
        rec = ReconnectReconciler(
            client=client,
            on_fill=lambda f: None,
            tracked_symbols=["BTCUSDT"],
        )
        rec.reconcile()

        # reconcile should have queried open orders
        assert len(rsps_lib.calls) == 1
