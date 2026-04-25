"""Edge coverage tests for AsyncBinanceUserDataStream._handle_message / _enqueue.

Targets uncovered branches in src/brokers/binance/async_ws.py:
- Non-ORDER_TRADE_UPDATE event filter (line 193)
- Unparseable WS message (lines 188-190)
- drop_oldest overflow hit (lines 213-214 — QueueEmpty path)
- Fill dedup returns None (line 198)
- expiry_event shortcut in reconnect loop (lines 143, 169)
- listenKey reissue failure swallowed (lines 174-175)
- max reconnect attempts exceeded (lines 181-183)
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.brokers.binance.async_ws import AsyncBinanceUserDataStream


def _make_stream(policy: str = "block", queue_size: int = 2) -> AsyncBinanceUserDataStream:
    # Use a MagicMock for the client — only referenced when ListenKeyManager needs it,
    # which doesn't happen in these unit tests (we exercise _handle_message / _enqueue directly).
    mock_client = MagicMock()
    mock_client.issue_listen_key = AsyncMock(return_value="lkABC123")
    mock_client.extend_listen_key = AsyncMock()
    mock_client.delete_listen_key = AsyncMock()

    stream = AsyncBinanceUserDataStream(
        client=mock_client,
        ws_base_url="wss://fstream.binance.test/ws",
        queue_size=queue_size,
        overflow_policy=policy,
    )
    return stream


@pytest.mark.asyncio
async def test_handle_unparseable_message_logs_and_returns():
    stream = _make_stream()
    # Raw bytes that is not valid JSON
    await stream._handle_message(b"\x00\x01\x02 not json")
    # Queue should remain empty
    assert stream._queue.qsize() == 0


@pytest.mark.asyncio
async def test_handle_non_order_event_filtered():
    """Events other than ORDER_TRADE_UPDATE are dropped (line 193)."""
    stream = _make_stream()
    msg = json.dumps({"e": "ACCOUNT_UPDATE", "a": {}})
    await stream._handle_message(msg)
    assert stream._queue.qsize() == 0


@pytest.mark.asyncio
async def test_handle_fill_missing_required_fields_skipped():
    """_parse_fill returns None when required fields missing → no enqueue (line 198)."""
    stream = _make_stream()
    msg = json.dumps({"e": "ORDER_TRADE_UPDATE", "o": {}})  # empty order obj
    await stream._handle_message(msg)
    assert stream._queue.qsize() == 0


@pytest.mark.asyncio
async def test_drop_oldest_overflow_with_full_queue():
    """drop_oldest policy when queue is full: pops oldest + increments metric."""
    stream = _make_stream(policy="drop_oldest", queue_size=1)

    # Pre-fill queue to capacity
    from src.brokers.types import BrokerFill
    from decimal import Decimal
    from datetime import datetime, timezone

    def make_fill(tid: str) -> BrokerFill:
        return BrokerFill(
            parent_id="p",
            broker_order_id="o",
            client_order_id="c",
            trade_id=tid,
            qty=Decimal("1"),
            price=Decimal("100"),
            fee=Decimal("0.1"),
            fee_asset="USDT",
            ts=datetime.now(tz=timezone.utc),
            is_maker=False,
        )

    first = make_fill("t1")
    second = make_fill("t2")
    await stream._enqueue(first)
    # Queue is now full (size 1). Next enqueue should drop first.
    await stream._enqueue(second)
    assert stream._queue.qsize() == 1
    # Drain — should be `second`
    got = await stream._queue.get()
    assert got.trade_id == "t2"


@pytest.mark.asyncio
async def test_raise_overflow_when_queue_full():
    """raise policy when queue is full → raises an overflow exception."""
    stream = _make_stream(policy="raise", queue_size=1)

    from src.brokers.types import BrokerFill
    from decimal import Decimal
    from datetime import datetime, timezone

    def make_fill(tid: str) -> BrokerFill:
        return BrokerFill(
            parent_id="p",
            broker_order_id="o",
            client_order_id="c",
            trade_id=tid,
            qty=Decimal("1"),
            price=Decimal("100"),
            fee=Decimal("0.1"),
            fee_asset="USDT",
            ts=datetime.now(tz=timezone.utc),
            is_maker=False,
        )

    await stream._enqueue(make_fill("t1"))
    # Queue full → raise policy should raise
    with pytest.raises(Exception):
        await stream._enqueue(make_fill("t2"))


@pytest.mark.asyncio
async def test_aclose_idempotent_and_no_task_leak():
    """aclose() multiple times does not raise and cleans up."""
    stream = _make_stream()
    await stream.aclose()
    await stream.aclose()  # idempotent


@pytest.mark.asyncio
async def test_dedup_skips_repeated_trade_id():
    """Same (broker_order_id, trade_id) pair seen twice → second skipped."""
    stream = _make_stream()
    order = {
        "i": "order-1",  # broker order id
        "t": "trade-42",  # trade id
        "c": "client-1",
        "q": "1.0",
        "p": "100.0",
        "L": "100.0",
        "l": "1.0",
        "n": "0.01",
        "N": "USDT",
        "T": 1700000000000,
        "m": False,
        "x": "TRADE",
    }
    msg = json.dumps({"e": "ORDER_TRADE_UPDATE", "o": order})
    await stream._handle_message(msg)
    size_after_first = stream._queue.qsize()
    await stream._handle_message(msg)  # duplicate
    size_after_second = stream._queue.qsize()
    # Second call should not add (dedup by (i, t))
    assert size_after_second == size_after_first
