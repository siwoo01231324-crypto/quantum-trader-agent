"""Partial fill sequence tests: 50% → 75% → 90% → 100%.

Tests accumulation of qty, avg_price, and fees across partial fills.
"""
from __future__ import annotations

import time
from decimal import Decimal

import pytest

from src.brokers.binance.reconciler import ReconnectReconciler
from src.brokers.binance.rest import BinanceFuturesClient
from src.brokers.rate_limiter import RateLimiter
from src.brokers.types import BrokerFill

BASE_URL = "https://testnet.binancefuture.com"


def _make_client() -> BinanceFuturesClient:
    rl = RateLimiter()
    rl.register_bucket("weight", rate=100.0, capacity=6000.0)
    rl.register_bucket("orders_1m", rate=20.0, capacity=1200.0)
    rl.register_bucket("orders_10s", rate=30.0, capacity=300.0)
    client = BinanceFuturesClient(
        api_key="k", secret="s", base_url=BASE_URL, rate_limiter=rl
    )
    client._last_sync = time.monotonic()
    return client


def _make_reconciler() -> tuple[ReconnectReconciler, list[BrokerFill]]:
    client = _make_client()
    received: list[BrokerFill] = []
    rec = ReconnectReconciler(client=client, on_fill=received.append)
    return rec, received


def _make_event(order_id: str, trade_id: str, qty: str, price: str, fee: str) -> dict:
    return {
        "x": "TRADE",
        "i": order_id,
        "t": trade_id,
        "c": "cid-strat",
        "s": "BTCUSDT",
        "l": qty,
        "L": price,
        "n": fee,
        "N": "USDT",
        "T": 1700000000000,
        "m": False,
    }


class TestPartialFillSequence:
    """Simulate 50/75/90/100% fill sequence for a BTCUSDT order."""

    @pytest.fixture
    def fill_sequence(self) -> list[tuple[str, str, str]]:
        """(trade_id, qty, price, fee) for 4 partial fills summing to 0.004 BTC."""
        return [
            ("t001", "0.002",   "50000.0", "0.05"),   # 50%
            ("t002", "0.001",   "50010.0", "0.025"),   # 75%
            ("t003", "0.0004",  "50005.0", "0.010"),   # 90%
            ("t004", "0.0006",  "49995.0", "0.015"),   # 100%
        ]

    def test_all_fills_dispatched(self, fill_sequence):
        rec, received = _make_reconciler()
        for trade_id, qty, price, fee in fill_sequence:
            fill = rec.on_trade_event(_make_event("order-1", trade_id, qty, price, fee))
            assert fill is not None
            received.append(fill)

        assert len(received) == 4
        cumulative_qty = sum(f.qty for f in received)
        assert cumulative_qty == Decimal("0.004")

    def test_weighted_avg_price(self, fill_sequence):
        rec, received = _make_reconciler()
        for trade_id, qty, price, fee in fill_sequence:
            fill = rec.on_trade_event(_make_event("order-2", trade_id, qty, price, fee))
            assert fill is not None
            received.append(fill)

        total_qty = sum(f.qty for f in received)
        weighted_sum = sum(f.qty * f.price for f in received)
        avg_price = weighted_sum / total_qty

        expected_avg = (
            Decimal("0.002")  * Decimal("50000.0")
            + Decimal("0.001")  * Decimal("50010.0")
            + Decimal("0.0004") * Decimal("50005.0")
            + Decimal("0.0006") * Decimal("49995.0")
        ) / Decimal("0.004")

        assert avg_price == pytest.approx(float(expected_avg), rel=1e-6)

    def test_cumulative_fee(self, fill_sequence):
        rec, received = _make_reconciler()
        for trade_id, qty, price, fee in fill_sequence:
            fill = rec.on_trade_event(_make_event("order-3", trade_id, qty, price, fee))
            assert fill is not None
            received.append(fill)

        total_fee = sum(f.fee for f in received)
        assert total_fee == Decimal("0.1")

    def test_no_duplicate_fills_in_sequence(self, fill_sequence):
        """Replaying the same fill events after reconnect must not double-count."""
        rec, received = _make_reconciler()

        for trade_id, qty, price, fee in fill_sequence:
            rec.on_trade_event(_make_event("order-4", trade_id, qty, price, fee))

        # Replay same events (simulating WS reconnect without dedup reset)
        duplicates_dispatched = 0
        for trade_id, qty, price, fee in fill_sequence:
            fill = rec.on_trade_event(_make_event("order-4", trade_id, qty, price, fee))
            if fill is not None:
                duplicates_dispatched += 1

        assert duplicates_dispatched == 0
