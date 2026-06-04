"""Unit tests for src.live.fill_consumer.run_bitget_fill_consumer (P4b).

Smaller focused suite than the Binance equivalent — the consumer body is a
near-identical copy (only logger labels + remediation message differ), so
this file verifies the wiring (write to WAL + dedup + position_store ctx)
without re-testing the entire reconnect-backoff state machine.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.brokers.types import BrokerFill
from src.live.fill_consumer import run_bitget_fill_consumer
from src.live.wal import WAL, replay


def _fill(*, coid: str = "bg-coid-1", oid: str = "bg-oid-1",
          trade_id: str = "tid-1", qty: str = "0.001",
          price: str = "67500.0") -> BrokerFill:
    return BrokerFill(
        parent_id=coid,
        broker_order_id=oid,
        client_order_id=coid,
        trade_id=trade_id,
        qty=Decimal(qty),
        price=Decimal(price),
        fee=Decimal("0.027"),
        fee_asset="USDT",
        ts=datetime.now(tz=timezone.utc),
        is_maker=False,
    )


async def _stream_from(fills: list[BrokerFill]):
    for f in fills:
        yield f


@pytest.mark.asyncio
async def test_bitget_consumer_writes_order_filled_wal(tmp_path: Path):
    wal = WAL(tmp_path / "wal.jsonl", observer=None)
    store = MagicMock()
    store.resolve_order_context.return_value = ("BTCUSDT", "buy", "test-strategy")
    stop_event = asyncio.Event()

    fills = [_fill()]

    def factory():
        return _stream_from(fills)

    await run_bitget_fill_consumer(
        factory, wal=wal, position_store=store, stop_event=stop_event,
    )

    # WAL has exactly one order_filled with the right payload.
    events, _ = replay(tmp_path / "wal.jsonl")
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "order_filled"
    assert ev.payload["symbol"] == "BTCUSDT"
    assert ev.payload["broker_order_id"] == "bg-oid-1"
    assert ev.payload["strategy_id"] == "test-strategy"


@pytest.mark.asyncio
async def test_bitget_consumer_dedups_same_trade_id(tmp_path: Path):
    wal = WAL(tmp_path / "wal.jsonl", observer=None)
    store = MagicMock()
    store.resolve_order_context.return_value = ("BTCUSDT", "buy", "sid")
    stop_event = asyncio.Event()

    # Two BrokerFill rows with identical (broker_order_id, trade_id).
    fills = [_fill(), _fill()]

    def factory():
        return _stream_from(fills)

    await run_bitget_fill_consumer(
        factory, wal=wal, position_store=store, stop_event=stop_event,
    )

    events, _ = replay(tmp_path / "wal.jsonl")
    assert len(events) == 1  # dedup'd


@pytest.mark.asyncio
async def test_bitget_consumer_emits_without_strategy_id_when_context_missing(
    tmp_path: Path, caplog,
):
    wal = WAL(tmp_path / "wal.jsonl", observer=None)
    store = MagicMock()
    store.resolve_order_context.return_value = None
    stop_event = asyncio.Event()

    fills = [_fill(coid="orphan", oid="orphan-oid", trade_id="orphan-tid")]

    def factory():
        return _stream_from(fills)

    with caplog.at_level(logging.WARNING):
        await run_bitget_fill_consumer(
            factory, wal=wal, position_store=store, stop_event=stop_event,
        )

    events, _ = replay(tmp_path / "wal.jsonl")
    assert len(events) == 1
    # No strategy_id when context cannot be resolved.
    assert "strategy_id" not in events[0].payload
    # Warning was emitted for caller visibility.
    assert any("bitget_fill_consumer" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_bitget_consumer_stop_event_aborts_mid_stream(tmp_path: Path):
    wal = WAL(tmp_path / "wal.jsonl", observer=None)
    store = MagicMock()
    store.resolve_order_context.return_value = ("BTCUSDT", "buy", "sid")
    stop_event = asyncio.Event()

    async def slow_stream():
        yield _fill(trade_id="tid-1")
        # Set stop before next yield.
        stop_event.set()
        yield _fill(trade_id="tid-2")

    def factory():
        return slow_stream()

    await run_bitget_fill_consumer(
        factory, wal=wal, position_store=store, stop_event=stop_event,
    )

    # Only the first fill should have made it to WAL.
    events, _ = replay(tmp_path / "wal.jsonl")
    assert len(events) == 1
    assert events[0].payload["broker_order_id"] == "bg-oid-1"
