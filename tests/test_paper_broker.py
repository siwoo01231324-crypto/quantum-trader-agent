"""Tests for PaperBroker — 12 test cases covering Phase B-2 AC."""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.brokers.base import (
    HealthStatus,
    OrderAck,
    OrderRequest,
    OrderType,
    PositionSide,
)
from src.execution.base import MarketState, Side, Tick
from src.execution.mock_matching import MockMatchingEngine
from src.execution.paper_broker import PaperBroker
from src.live.types import OrderStatus
from src.ops.kill_switch import KillSwitch, KillSwitchTripped
from src.execution.base import TimeInForce
from src.live.wal import WAL


def _make_market(price: float = 100.0) -> MarketState:
    tick = Tick(
        symbol="BTCUSDT",
        bid=price - 0.5,
        ask=price + 0.5,
        last=price,
        volume=1000,
        ts=datetime.now(timezone.utc),
    )
    return MarketState(tick=tick)


def _make_req(
    side: Side = Side.BUY,
    qty: Decimal = Decimal("1"),
    price: Decimal | None = None,
    order_type: OrderType = OrderType.MARKET,
    client_order_id: str = "coid-1",
) -> OrderRequest:
    return OrderRequest(
        client_order_id=client_order_id,
        symbol="BTCUSDT",
        side=side,
        qty=qty,
        order_type=order_type,
        price=price,
        tif=TimeInForce.GTC,
    )


def _make_broker(tmp_path: Path, initial_balance: Decimal = Decimal("100000")) -> PaperBroker:
    wal = WAL(tmp_path / "wal.jsonl")
    ks = KillSwitch()
    engine = MockMatchingEngine()
    broker = PaperBroker(wal=wal, kill_switch=ks, matching_engine=engine, initial_balance=initial_balance)
    broker.update_market(_make_market())
    return broker


# --- test 1: market order fills immediately ---
@pytest.mark.asyncio
async def test_market_order_fills(tmp_path):
    broker = _make_broker(tmp_path)
    req = _make_req()
    ack = await broker.place_order(req)
    assert ack.status == OrderStatus.FILLED.value
    assert ack.qty == Decimal("1")
    assert ack.broker_order_id != ""


# --- test 2: position updated after buy ---
@pytest.mark.asyncio
async def test_position_updated_after_buy(tmp_path):
    broker = _make_broker(tmp_path)
    await broker.place_order(_make_req())
    positions = await broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "BTCUSDT"
    assert positions[0].side == PositionSide.LONG
    assert positions[0].qty == Decimal("1")


# --- test 3: balance decremented after buy ---
@pytest.mark.asyncio
async def test_balance_decremented_after_buy(tmp_path):
    broker = _make_broker(tmp_path, initial_balance=Decimal("100000"))
    ack = await broker.place_order(_make_req(qty=Decimal("1")))
    balances = await broker.get_balance()
    usdt = next(b for b in balances if b.asset == "USDT")
    # cost = 1 * 100 = 100, fee = 1 * 100 * 5/10000 = 0.05
    assert usdt.free < Decimal("100000")


# --- test 4: kill switch blocks order ---
@pytest.mark.asyncio
async def test_kill_switch_blocks_order(tmp_path):
    wal = WAL(tmp_path / "wal.jsonl")
    ks = KillSwitch()
    ks.trip(reason="manual", source="test")
    broker = PaperBroker(wal=wal, kill_switch=ks)
    broker.update_market(_make_market())
    ack = await broker.place_order(_make_req())
    assert ack.status == OrderStatus.REJECTED.value
    assert ack.reject_reason == "KILL_SWITCH"


# --- test 5: WAL write failure trips kill switch and returns REJECTED ---
@pytest.mark.asyncio
async def test_wal_write_failure_trips_kill_switch(tmp_path):
    from src.live.wal import WALWriteFailed

    class BrokenWAL(WAL):
        def write(self, event):
            raise WALWriteFailed("disk full")

    ks = KillSwitch()
    broker = PaperBroker(wal=BrokenWAL(tmp_path / "wal.jsonl"), kill_switch=ks)
    broker.update_market(_make_market())
    ack = await broker.place_order(_make_req())
    assert ack.status == OrderStatus.REJECTED.value
    assert ack.reject_reason == "WAL_WRITE_FAIL"
    assert ks.tripped


# --- test 6: no market state → REJECTED with NO_MARKET_STATE ---
@pytest.mark.asyncio
async def test_no_market_state_rejected(tmp_path):
    wal = WAL(tmp_path / "wal.jsonl")
    ks = KillSwitch()
    broker = PaperBroker(wal=wal, kill_switch=ks)
    # do NOT call update_market
    ack = await broker.place_order(_make_req())
    assert ack.status == OrderStatus.REJECTED.value
    assert ack.reject_reason == "NO_MARKET_STATE"


# --- test 7: limit order miss → REJECTED ---
@pytest.mark.asyncio
async def test_limit_order_miss_rejected(tmp_path):
    broker = _make_broker(tmp_path)
    # market is at 100, bid=99.5, ask=100.5
    # buy limit at 50 — below ask, not crossable
    req = _make_req(
        order_type=OrderType.LIMIT,
        price=Decimal("50"),
        side=Side.BUY,
    )
    ack = await broker.place_order(req)
    assert ack.status == OrderStatus.REJECTED.value
    assert ack.reject_reason == "LIMIT_PRICE_MISS"


# --- test 8: cancel_order is no-op ---
@pytest.mark.asyncio
async def test_cancel_order_noop(tmp_path):
    broker = _make_broker(tmp_path)
    # should not raise
    await broker.cancel_order(client_order_id="coid-1", symbol="BTCUSDT")


# --- test 9: stream_fills yields fills ---
@pytest.mark.asyncio
async def test_stream_fills_yields_fill(tmp_path):
    broker = _make_broker(tmp_path)
    await broker.place_order(_make_req())
    stream = broker.stream_fills()
    fill = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
    assert fill.qty == Decimal("1")
    assert isinstance(fill.price, Decimal)
    assert isinstance(fill.fee, Decimal)


# --- test 10: health_check returns OK normally, DOWN when kill switch tripped ---
@pytest.mark.asyncio
async def test_health_check(tmp_path):
    broker = _make_broker(tmp_path)
    assert await broker.health_check() == HealthStatus.OK
    broker._kill_switch.trip(reason="test", source="test")
    assert await broker.health_check() == HealthStatus.DOWN


# --- test 11: from_wal restores position state ---
@pytest.mark.asyncio
async def test_from_wal_restores_state(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    ks = KillSwitch()
    broker = PaperBroker(wal=WAL(wal_path), kill_switch=ks)
    broker.update_market(_make_market())
    await broker.place_order(_make_req(qty=Decimal("2")))

    # Restore
    ks2 = KillSwitch()
    restored = PaperBroker.from_wal(wal_path, kill_switch=ks2)
    positions = await restored.get_positions()
    assert len(positions) == 1
    assert positions[0].qty == Decimal("2")


# --- test 12: WAL payload uses str(Decimal), not float ---
@pytest.mark.asyncio
async def test_wal_payload_decimal_str(tmp_path):
    import json

    wal_path = tmp_path / "wal.jsonl"
    broker = _make_broker(tmp_path)
    broker._wal = WAL(wal_path)
    await broker.place_order(_make_req(qty=Decimal("0.5")))

    lines = wal_path.read_text().strip().split("\n")
    for line in lines:
        data = json.loads(line)
        payload = data.get("payload", {})
        for key in ("qty", "fill_qty", "fill_price", "fees"):
            if key in payload and payload[key] is not None:
                # must be string, not float
                assert isinstance(payload[key], str), f"{key} should be str, got {type(payload[key])}"
                # must parse as Decimal without precision loss
                Decimal(payload[key])
