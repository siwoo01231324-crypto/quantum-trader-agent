"""Tests for cs_rebalance_dispatch — strategy weights → broker.place_order (#218 Phase 2)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest

from brokers.base import OrderAck, OrderRequest
from portfolio.cs_rebalance_dispatch import (
    RebalanceReport,
    dispatch_rebalance,
    telegram_digest_message,
)
from portfolio.weights_to_orders import KRX_LOT


class MockBroker:
    """Minimal AsyncBrokerAdapter mock — records all place_order calls."""

    def __init__(self, reject_symbols: set[str] | None = None) -> None:
        self.placed: list[OrderRequest] = []
        self._reject_symbols = reject_symbols or set()

    async def place_order(self, req: OrderRequest) -> OrderAck:
        self.placed.append(req)
        status = "REJECTED" if req.symbol in self._reject_symbols else "FILLED"
        return OrderAck(
            broker_order_id=f"mock-{len(self.placed)}",
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            status=status,
            ts=datetime.now(timezone.utc),
            qty=req.qty,
            price=Decimal("100"),
            reject_reason="MOCK_REJECT" if req.symbol in self._reject_symbols else None,
        )


def _series(d: dict) -> pd.Series:
    return pd.Series(d)


@pytest.mark.asyncio
async def test_dispatch_initial_buy_all_picks():
    broker = MockBroker()
    target = _series({"005930": 0.5, "000660": 0.5})
    prices = _series({"005930": 80_000, "000660": 200_000})
    report = await dispatch_rebalance(
        "cs_test_kr",
        target_weights=target,
        current_positions={},
        prices=prices,
        total_capital=10_000_000,
        broker=broker,
        lot_spec=KRX_LOT,
    )
    assert report.summary["n_intents"] == 2
    assert report.summary["n_submitted"] == 2
    assert len(broker.placed) == 2
    syms = {r.symbol for r in broker.placed}
    assert syms == {"005930", "000660"}
    for r in broker.placed:
        assert r.side.value == "BUY"


@pytest.mark.asyncio
async def test_dispatch_handles_rejected_orders():
    """broker REJECTED → report.rejected 에 기록."""
    broker = MockBroker(reject_symbols={"000660"})
    target = _series({"005930": 0.5, "000660": 0.5})
    prices = _series({"005930": 80_000, "000660": 200_000})
    report = await dispatch_rebalance(
        "cs_test_kr",
        target_weights=target,
        current_positions={},
        prices=prices,
        total_capital=10_000_000,
        broker=broker,
        lot_spec=KRX_LOT,
    )
    assert report.summary["n_submitted"] == 1
    assert report.summary["n_rejected"] == 1
    rejected = report.rejected[0]
    assert rejected.symbol == "000660"
    assert rejected.reject_reason == "MOCK_REJECT"


@pytest.mark.asyncio
async def test_dispatch_handles_broker_exception():
    """place_order 가 예외를 raise 해도 다른 종목 주문은 계속."""

    class FlakyBroker(MockBroker):
        async def place_order(self, req: OrderRequest) -> OrderAck:
            if req.symbol == "FLAKY":
                raise RuntimeError("simulated broker timeout")
            return await super().place_order(req)

    broker = FlakyBroker()
    target = _series({"OK1": 0.4, "FLAKY": 0.3, "OK2": 0.3})
    prices = _series({"OK1": 100_000, "FLAKY": 50_000, "OK2": 200_000})
    report = await dispatch_rebalance(
        "cs_test_kr",
        target_weights=target,
        current_positions={},
        prices=prices,
        total_capital=10_000_000,
        broker=broker,
        lot_spec=KRX_LOT,
    )
    assert report.summary["n_submitted"] == 2
    assert report.summary["n_skipped_exception"] == 1
    assert report.skipped[0].symbol == "FLAKY"


@pytest.mark.asyncio
async def test_dispatch_no_orders_when_already_balanced():
    """target == current → 발주 없음."""
    broker = MockBroker()
    target = _series({"AAA": 0.5})
    prices = _series({"AAA": 100_000})
    # current 이 정확히 49 (lot=1 floor)
    report = await dispatch_rebalance(
        "cs_test_kr",
        target_weights=target,
        current_positions={"AAA": 49.0},
        prices=prices,
        total_capital=10_000_000,
        broker=broker,
        lot_spec=KRX_LOT,
    )
    assert report.summary == {"reason": "no_orders_needed", "n_target": 1}
    assert broker.placed == []


@pytest.mark.asyncio
async def test_dispatch_full_liquidation_when_target_zero():
    """target weights = 0 → 보유 전량 청산 발주."""
    broker = MockBroker()
    target = _series({"AAA": 0.0})
    prices = _series({"AAA": 100_000})
    report = await dispatch_rebalance(
        "cs_test_kr",
        target_weights=target,
        current_positions={"AAA": 50.0},
        prices=prices,
        total_capital=10_000_000,
        broker=broker,
        lot_spec=KRX_LOT,
    )
    assert report.summary["n_submitted"] == 1
    assert broker.placed[0].side.value == "SELL"
    assert broker.placed[0].qty == Decimal("50")


@pytest.mark.asyncio
async def test_dispatch_rotation_buys_new_sells_dropped():
    """이번 주 picks 와 직전 주 picks 가 다를 때 rotation 발주."""
    broker = MockBroker()
    target = _series({"NEW1": 0.5, "NEW2": 0.5, "OLD": 0.0})
    prices = _series({"NEW1": 100_000, "NEW2": 200_000, "OLD": 50_000})
    report = await dispatch_rebalance(
        "cs_test_kr",
        target_weights=target,
        current_positions={"OLD": 100.0},
        prices=prices,
        total_capital=10_000_000,
        broker=broker,
        lot_spec=KRX_LOT,
    )
    sides = [(r.symbol, r.side.value) for r in broker.placed]
    assert ("NEW1", "BUY") in sides
    assert ("NEW2", "BUY") in sides
    assert ("OLD", "SELL") in sides
    assert report.summary["n_submitted"] == 3


def test_telegram_digest_message_format():
    target = _series({"BUY1": 0.3, "BUY2": 0.3, "HOLD": 0.4})
    current = {"HOLD": 50.0, "SELL_THIS": 30.0}
    rep = RebalanceReport(strategy_id="cs_test_kr",
                          summary={"n_submitted": 3, "n_rejected": 0})
    msg = telegram_digest_message(rep, target, current)
    assert "cs_test_kr" in msg
    assert "매수 2종" in msg
    assert "매도 1종" in msg
    assert "유지 1종" in msg
    assert "BUY1" in msg or "BUY2" in msg
    assert "SELL_THIS" in msg
