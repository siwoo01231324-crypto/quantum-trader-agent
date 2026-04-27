"""Stage 4.4: WAL single-writer serialisation policy (Architect note #6, #105).

ADR: asyncio.Lock vs single-writer task structure.
Decision: single-writer task (loop.py:167-186 consumer is the only WAL caller).
Rationale: Lock overhead unnecessary when consumer is the sole writer.
If WS fill listener writes to WAL, it must enqueue via asyncio.Queue → consumer writes.

These tests verify:
  1. Sequential WAL writes in a single asyncio task preserve order and produce no corruption.
  2. Multiple sequential writes produce valid JSONL (each line parseable).
  3. Windows fsync atomicity: write + fsync per line (WAL already does this).
  4. asyncio.Queue → single-consumer pattern: fills enqueued by producer, consumed once.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.live.types import EVENT_ORDER_ACKED, EVENT_TRACKING_SAMPLE, WALEvent
from src.live.wal import WAL, replay


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 1. Sequential writes in single task preserve order + no corruption
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_task_sequential_writes_preserve_order():
    """Single asyncio task writing N events → all events present, order preserved."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wal = WAL(Path(tmpdir) / "wal.jsonl")
        n = 20
        for i in range(n):
            wal.write(WALEvent(
                ts=_ts(),
                event_type=EVENT_ORDER_ACKED,
                payload={"seq": i, "broker_order_id": f"bo{i}", "client_order_id": f"cid{i}",
                         "ack_ts": _ts(), "status": "NEW", "origin": "executor"},
            ))

        events, corruptions = replay(wal.path)
        assert len(corruptions) == 0, f"Corruptions found: {corruptions}"
        assert len(events) == n
        seqs = [ev.payload["seq"] for ev in events]
        assert seqs == list(range(n)), "Event order not preserved"


# ---------------------------------------------------------------------------
# 2. Mixed event types (order_acked + tracking_sample) — all parseable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mixed_event_types_all_parseable():
    with tempfile.TemporaryDirectory() as tmpdir:
        wal = WAL(Path(tmpdir) / "wal.jsonl")
        for i in range(5):
            wal.write(WALEvent(
                ts=_ts(),
                event_type=EVENT_ORDER_ACKED,
                payload={"broker_order_id": f"bo{i}", "client_order_id": f"cid{i}",
                         "ack_ts": _ts(), "status": "NEW", "origin": "executor"},
            ))
            wal.write(WALEvent(
                ts=_ts(),
                event_type=EVENT_TRACKING_SAMPLE,
                payload={
                    "broker_order_id": f"bo{i}", "client_order_id": f"cid{i}",
                    "kis_fill_price": "70000", "sim_fill_price": "70050",
                    "kis_fill_qty": "10", "sim_fill_qty": "10",
                    "kis_fill_ts": _ts(), "sim_fill_ts": _ts(), "latency_ms": 5.0,
                },
            ))

        events, corruptions = replay(wal.path)
        assert len(corruptions) == 0
        assert len(events) == 10
        types = [ev.event_type for ev in events]
        # Alternating order preserved
        assert types[::2] == [EVENT_ORDER_ACKED] * 5
        assert types[1::2] == [EVENT_TRACKING_SAMPLE] * 5


# ---------------------------------------------------------------------------
# 3. Windows fsync atomicity: file closed + reopened preserves all lines
# ---------------------------------------------------------------------------

def test_fsync_per_write_atomicity():
    """Each WAL.write() opens, writes, fsyncs, closes. Reopen must see all lines."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "wal.jsonl"
        wal = WAL(path)
        for i in range(5):
            wal.write(WALEvent(
                ts=_ts(),
                event_type=EVENT_ORDER_ACKED,
                payload={"seq": i, "broker_order_id": f"bo{i}", "client_order_id": f"cid{i}",
                         "ack_ts": _ts(), "status": "NEW", "origin": "executor"},
            ))

        # Reopen independently and count lines
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        assert len(lines) == 5
        for line in lines:
            obj = json.loads(line)
            assert "event_type" in obj


# ---------------------------------------------------------------------------
# 4. asyncio.Queue → single consumer pattern: no dropped events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_queue_single_consumer_no_dropped_events():
    """WS fill listener enqueues via asyncio.Queue; consumer writes to WAL.
    All enqueued items must be written exactly once."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wal = WAL(Path(tmpdir) / "wal.jsonl")
        queue: asyncio.Queue[WALEvent] = asyncio.Queue()
        n = 10

        # Simulate WS fill listener producing events
        async def producer():
            for i in range(n):
                await queue.put(WALEvent(
                    ts=_ts(),
                    event_type=EVENT_TRACKING_SAMPLE,
                    payload={
                        "broker_order_id": f"bo{i}", "client_order_id": f"cid{i}",
                        "kis_fill_price": "70000", "sim_fill_price": "70000",
                        "kis_fill_qty": "1", "sim_fill_qty": "1",
                        "kis_fill_ts": _ts(), "sim_fill_ts": _ts(), "latency_ms": 1.0,
                    },
                ))
            await queue.put(None)  # sentinel

        # Single consumer writes to WAL
        async def consumer():
            while True:
                ev = await queue.get()
                if ev is None:
                    break
                wal.write(ev)

        await asyncio.gather(producer(), consumer())

        events, corruptions = replay(wal.path)
        assert len(corruptions) == 0
        assert len(events) == n, f"Expected {n} events, got {len(events)}"
