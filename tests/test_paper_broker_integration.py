"""Integration tests for PaperBroker — 8 test cases covering Phase B-3 AC.

These tests exercise the full stack: PaperBroker + MockMatchingEngine + WAL + KillSwitch.
They are distinct from unit tests in test_paper_broker.py and test_mock_matching.py.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.brokers.base import (
    HealthStatus,
    OrderRequest,
    OrderType,
    PositionSide,
)
from src.execution.base import MarketState, Side, Tick, TimeInForce
from src.execution.mock_matching import MockMatchingEngine
from src.execution.paper_broker import PaperBroker
from src.live.types import OrderStatus
from src.live.wal import WAL, WALWriteFailed
from src.ops.kill_switch import KillSwitch


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _market(price: float = 50000.0) -> MarketState:
    tick = Tick(
        symbol="BTCUSDT",
        bid=price - 1.0,
        ask=price + 1.0,
        last=price,
        volume=10000,
        ts=datetime.now(timezone.utc),
    )
    return MarketState(tick=tick)


def _req(
    *,
    side: Side = Side.BUY,
    qty: str = "0.1",
    order_type: OrderType = OrderType.MARKET,
    price: str | None = None,
    client_order_id: str = "coid-int-1",
    emergency_exit: bool = False,
) -> OrderRequest:
    return OrderRequest(
        client_order_id=client_order_id,
        symbol="BTCUSDT",
        side=side,
        qty=Decimal(qty),
        order_type=order_type,
        price=Decimal(price) if price is not None else None,
        tif=TimeInForce.GTC,
        emergency_exit=emergency_exit,
    )


def _broker(wal_path: Path, initial_balance: str = "100000") -> PaperBroker:
    wal = WAL(wal_path)
    ks = KillSwitch()
    engine = MockMatchingEngine()
    broker = PaperBroker(
        wal=wal,
        kill_switch=ks,
        matching_engine=engine,
        initial_balance=Decimal(initial_balance),
    )
    broker.update_market(_market())
    return broker


# ---------------------------------------------------------------------------
# test_full_flow_market_buy
# Verifies: ack=FILLED, position updated, balance decremented, WAL has submit+fill events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_flow_market_buy(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    broker = _broker(wal_path)

    ack = await broker.place_order(_req(qty="1"))

    assert ack.status == OrderStatus.FILLED.value
    assert ack.qty == Decimal("1")
    assert ack.price == Decimal("50000")

    positions = await broker.get_positions()
    assert len(positions) == 1
    assert positions[0].side == PositionSide.LONG
    assert positions[0].qty == Decimal("1")

    balances = await broker.get_balance()
    usdt = next(b for b in balances if b.asset == "USDT")
    # cost=50000, fee=50000*0.05/100=25
    assert usdt.free == Decimal("100000") - Decimal("50000") - Decimal("25")

    events = [json.loads(l) for l in wal_path.read_text().splitlines() if l]
    types = [e["event_type"] for e in events]
    assert "order_submitted" in types
    assert "order_filled" in types


# ---------------------------------------------------------------------------
# test_full_flow_limit_buy_fillable
# price >= ask → fills; WAL records fill with correct fill_price
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_flow_limit_buy_fillable(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    broker = _broker(wal_path)  # market at 50000, ask=50001

    ack = await broker.place_order(
        _req(order_type=OrderType.LIMIT, price="50001", qty="0.5")
    )

    assert ack.status == OrderStatus.FILLED.value

    positions = await broker.get_positions()
    assert positions[0].qty == Decimal("0.5")

    events = [json.loads(l) for l in wal_path.read_text().splitlines() if l]
    fill_events = [e for e in events if e["event_type"] == "order_filled"]
    assert len(fill_events) == 1
    assert Decimal(fill_events[0]["payload"]["fill_price"]) == Decimal("50000")


# ---------------------------------------------------------------------------
# test_full_flow_limit_buy_unfillable
# price < ask → REJECTED with LIMIT_PRICE_MISS, WAL records rejection, no position
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_flow_limit_buy_unfillable(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    broker = _broker(wal_path)  # ask=50001

    ack = await broker.place_order(
        _req(order_type=OrderType.LIMIT, price="49000", qty="0.5")
    )

    assert ack.status == OrderStatus.REJECTED.value
    assert ack.reject_reason == "LIMIT_PRICE_MISS"

    positions = await broker.get_positions()
    assert positions == []

    events = [json.loads(l) for l in wal_path.read_text().splitlines() if l]
    reject_events = [e for e in events if e["event_type"] == "order_rejected"]
    assert any(e["payload"]["reject_reason"] == "LIMIT_PRICE_MISS" for e in reject_events)


# ---------------------------------------------------------------------------
# test_idempotency_key_in_wal
# Each order's client_order_id appears in WAL so replayed state matches
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idempotency_key_in_wal(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    broker = _broker(wal_path)

    for i in range(3):
        await broker.place_order(_req(client_order_id=f"coid-{i}", qty="0.1"))

    events = [json.loads(l) for l in wal_path.read_text().splitlines() if l]
    submitted_ids = [
        e["payload"]["client_order_id"]
        for e in events
        if e["event_type"] == "order_submitted"
    ]
    assert sorted(submitted_ids) == ["coid-0", "coid-1", "coid-2"]


# ---------------------------------------------------------------------------
# test_replay_round_trip_3_events
# Submit 3 fills, restore via from_wal, assert positions and balance identical
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_replay_round_trip_3_events(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    broker = _broker(wal_path)

    for i in range(3):
        await broker.place_order(_req(client_order_id=f"rt-{i}", qty="1"))

    orig_positions = await broker.get_positions()
    orig_balances = await broker.get_balance()

    restored = PaperBroker.from_wal(wal_path, kill_switch=KillSwitch())
    rest_positions = await restored.get_positions()
    rest_balances = await restored.get_balance()

    assert len(rest_positions) == len(orig_positions)
    assert rest_positions[0].qty == orig_positions[0].qty
    assert rest_positions[0].side == orig_positions[0].side

    orig_usdt = next(b for b in orig_balances if b.asset == "USDT")
    rest_usdt = next(b for b in rest_balances if b.asset == "USDT")
    assert rest_usdt.free == orig_usdt.free


# ---------------------------------------------------------------------------
# test_kill_switch_full_path_emergency_exit
# Tripped KS blocks normal orders; emergency_exit=True bypasses and fills
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kill_switch_full_path_emergency_exit(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal = WAL(wal_path)
    ks = KillSwitch()
    broker = PaperBroker(wal=wal, kill_switch=ks)
    broker.update_market(_market())

    ks.trip(reason="drawdown", source="auto:dd")

    # normal order → REJECTED
    blocked = await broker.place_order(_req(client_order_id="blocked"))
    assert blocked.status == OrderStatus.REJECTED.value
    assert blocked.reject_reason == "KILL_SWITCH"

    # emergency_exit order → FILLED (liquidation whitelist)
    exit_ack = await broker.place_order(
        _req(client_order_id="exit", side=Side.SELL, emergency_exit=True)
    )
    assert exit_ack.status == OrderStatus.FILLED.value

    # health_check reflects tripped state
    assert await broker.health_check() == HealthStatus.DOWN


# ---------------------------------------------------------------------------
# test_wal_corruption_recovery
# WAL file with one corrupt line; from_wal skips it and restores valid fills
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wal_corruption_recovery(tmp_path):
    wal_path = tmp_path / "wal.jsonl"

    # write a valid fill event manually
    valid_event = {
        "ts": "2026-04-25T09:00:00+00:00",
        "event_type": "order_filled",
        "schema_version": 1,
        "payload": {
            "client_order_id": "c1",
            "broker_order_id": "paper-0",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "qty": "1",
            "fill_price": "50000",
            "fill_qty": "1",
            "fees": "25",
            "fee_asset": "USDT",
            "ack_latency_ms": 0.1,
            "trade_id": "0",
            "server_ts": None,
        },
    }
    corrupt_line = "{not valid json!!!"
    with wal_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(valid_event) + "\n")
        f.write(corrupt_line + "\n")

    restored = PaperBroker.from_wal(wal_path, kill_switch=KillSwitch())

    positions = await restored.get_positions()
    assert len(positions) == 1
    assert positions[0].qty == Decimal("1")
    assert positions[0].side == PositionSide.LONG


# ---------------------------------------------------------------------------
# test_stream_fills_consumes_queue
# Place multiple orders; stream_fills yields each fill in order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_fills_consumes_queue(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    broker = _broker(wal_path)

    qtys = ["0.1", "0.2", "0.3"]
    for i, q in enumerate(qtys):
        await broker.place_order(_req(client_order_id=f"sf-{i}", qty=q))

    stream = broker.stream_fills()
    received = []
    for _ in qtys:
        fill = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
        received.append(fill)

    assert len(received) == 3
    assert [str(f.qty) for f in received] == qtys
