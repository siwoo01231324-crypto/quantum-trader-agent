"""WS tests for Binance async user-data stream (C4).

Uses in-process websockets.serve for a fake WS server (network-zero on localhost).
"""
from __future__ import annotations

import asyncio
import json
import pytest
import websockets
import websockets.server
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from src.brokers.binance.async_ws import AsyncBinanceUserDataStream, _parse_fill
from src.brokers.errors import ListenKeyExpiredError, WSDisconnectedError
from src.brokers.types import BrokerFill


# ── helpers ───────────────────────────────────────────────────────────────────

def _fill_msg(
    broker_order_id: str = "100",
    trade_id: str = "1",
    qty: str = "0.001",
    price: str = "42000",
) -> str:
    return json.dumps({
        "e": "ORDER_TRADE_UPDATE",
        "o": {
            "x": "TRADE",
            "i": broker_order_id,
            "t": trade_id,
            "c": "client-001",
            "s": "BTCUSDT",
            "T": 1700000000000,
            "l": qty,
            "L": price,
            "n": "0.001",
            "N": "USDT",
            "m": False,
        },
    })


def _make_mock_client(listen_key: str = "testkey123") -> MagicMock:
    client = MagicMock()
    client.issue_listen_key = AsyncMock(return_value=listen_key)
    client.extend_listen_key = AsyncMock()
    client.delete_listen_key = AsyncMock()
    return client


# ── _parse_fill unit tests ────────────────────────────────────────────────────

def test_parse_fill_returns_fill():
    o = {
        "x": "TRADE", "i": "100", "t": "1", "c": "cid", "s": "BTCUSDT",
        "T": 1700000000000, "l": "0.001", "L": "42000", "n": "0.001", "N": "USDT", "m": False,
    }
    seen: set = set()
    fill = _parse_fill(o, seen)
    assert fill is not None
    assert fill.broker_order_id == "100"
    assert fill.trade_id == "1"
    assert fill.qty == Decimal("0.001")


def test_parse_fill_deduplicates():
    o = {
        "x": "TRADE", "i": "100", "t": "1", "c": "cid", "s": "BTCUSDT",
        "T": 1700000000000, "l": "0.001", "L": "42000", "n": "0", "N": "USDT", "m": False,
    }
    seen: set = set()
    assert _parse_fill(o, seen) is not None
    assert _parse_fill(o, seen) is None  # duplicate


def test_parse_fill_ignores_non_trade():
    o = {"x": "NEW", "i": "100", "t": "1"}
    seen: set = set()
    assert _parse_fill(o, seen) is None


# ── WS stream tests ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stream_fills_normal(unused_tcp_port):
    """Test normal fill reception from a fake WS server."""
    fills_to_send = [_fill_msg("100", "1"), _fill_msg("101", "2")]
    received: list[BrokerFill] = []

    async def fake_server(websocket):
        for msg in fills_to_send:
            await websocket.send(msg)
        # Keep connection alive briefly then close
        await asyncio.sleep(0.2)

    async with websockets.serve(fake_server, "127.0.0.1", unused_tcp_port):
        client = _make_mock_client("testkey")
        stream = AsyncBinanceUserDataStream(
            client=client,
            ws_base_url=f"ws://127.0.0.1:{unused_tcp_port}",
            queue_size=100,
        )

        # Patch keepalive to be a no-op
        with patch.object(stream._listen_key_mgr, "start_keepalive"):
            with patch.object(stream._listen_key_mgr, "stop_keepalive", new=AsyncMock()):
                with patch.object(stream._listen_key_mgr, "delete", new=AsyncMock()):
                    async def collect():
                        async for fill in stream.stream_fills():
                            received.append(fill)
                            if len(received) >= 2:
                                stream._closed = True
                                break

                    await asyncio.wait_for(collect(), timeout=5.0)

    assert len(received) == 2
    assert received[0].broker_order_id == "100"
    assert received[1].broker_order_id == "101"


@pytest.mark.asyncio
async def test_stream_fills_reconnect_on_disconnect(unused_tcp_port):
    """After 1006 disconnect, stream reconnects and continues receiving fills."""
    connect_count = 0
    received: list[BrokerFill] = []

    async def fake_server(websocket):
        nonlocal connect_count
        connect_count += 1
        if connect_count == 1:
            # First connection: close abruptly
            await websocket.close(1006, "simulated disconnect")
        else:
            # Second connection: send a fill
            await websocket.send(_fill_msg("200", "10"))
            await asyncio.sleep(0.5)

    async with websockets.serve(fake_server, "127.0.0.1", unused_tcp_port):
        client = _make_mock_client("testkey")
        stream = AsyncBinanceUserDataStream(
            client=client,
            ws_base_url=f"ws://127.0.0.1:{unused_tcp_port}",
            queue_size=100,
        )

        with patch.object(stream._listen_key_mgr, "start_keepalive"):
            with patch.object(stream._listen_key_mgr, "stop_keepalive", new=AsyncMock()):
                with patch.object(stream._listen_key_mgr, "delete", new=AsyncMock()):
                    async def collect():
                        async for fill in stream.stream_fills():
                            received.append(fill)
                            stream._closed = True
                            break

                    await asyncio.wait_for(collect(), timeout=15.0)

    assert len(received) == 1
    assert received[0].broker_order_id == "200"
    assert connect_count >= 2


@pytest.mark.asyncio
async def test_listen_key_expiry_propagates():
    """When expiry_event is set (keepalive fails), ListenKeyExpiredError is raised."""
    client = _make_mock_client("testkey")
    stream = AsyncBinanceUserDataStream(
        client=client,
        ws_base_url="ws://127.0.0.1:19999",  # won't connect — expiry fires first
        queue_size=100,
    )

    with patch.object(stream._listen_key_mgr, "start_keepalive"):
        with patch.object(stream._listen_key_mgr, "stop_keepalive", new=AsyncMock()):
            with patch.object(stream._listen_key_mgr, "delete", new=AsyncMock()):
                # Simulate keepalive failure by setting the event immediately
                stream._expiry_event.set()

                with pytest.raises(ListenKeyExpiredError):
                    async for _ in stream.stream_fills():
                        pass  # pragma: no cover


@pytest.mark.asyncio
async def test_aclose_stops_stream(unused_tcp_port):
    """aclose() must cleanly stop the stream."""
    async def fake_server(websocket):
        await asyncio.sleep(10)  # keep alive

    async with websockets.serve(fake_server, "127.0.0.1", unused_tcp_port):
        client = _make_mock_client("testkey")
        stream = AsyncBinanceUserDataStream(
            client=client,
            ws_base_url=f"ws://127.0.0.1:{unused_tcp_port}",
            queue_size=100,
        )

        with patch.object(stream._listen_key_mgr, "start_keepalive"):
            with patch.object(stream._listen_key_mgr, "stop_keepalive", new=AsyncMock()):
                with patch.object(stream._listen_key_mgr, "delete", new=AsyncMock()):
                    async def run_and_close():
                        gen = stream.stream_fills()
                        task = asyncio.get_event_loop().create_task(gen.__anext__())
                        await asyncio.sleep(0.1)
                        await stream.aclose()
                        task.cancel()
                        try:
                            await task
                        except (asyncio.CancelledError, StopAsyncIteration):
                            pass

                    await asyncio.wait_for(run_and_close(), timeout=5.0)

    assert stream._closed is True


# ── overflow policy tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fill_queue_overflow_drop_oldest():
    """drop_oldest policy discards old fills when queue is full."""
    client = _make_mock_client()
    stream = AsyncBinanceUserDataStream(
        client=client,
        ws_base_url="ws://unused",
        queue_size=2,
        overflow_policy="drop_oldest",
    )
    # Pre-fill the queue
    fill1 = BrokerFill(
        parent_id="c1", broker_order_id="1", client_order_id="c1", trade_id="t1",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        fee_asset="USDT", ts=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        is_maker=False,
    )
    fill2 = BrokerFill(
        parent_id="c2", broker_order_id="2", client_order_id="c2", trade_id="t2",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        fee_asset="USDT", ts=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        is_maker=False,
    )
    fill3 = BrokerFill(
        parent_id="c3", broker_order_id="3", client_order_id="c3", trade_id="t3",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        fee_asset="USDT", ts=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        is_maker=False,
    )
    await stream._enqueue(fill1)
    await stream._enqueue(fill2)
    # Queue full — fill3 should drop fill1
    await stream._enqueue(fill3)
    assert stream._queue.qsize() == 2
    item = stream._queue.get_nowait()
    assert item.broker_order_id == "2"  # fill1 was dropped


@pytest.mark.asyncio
async def test_fill_queue_overflow_raise():
    """raise policy raises WSDisconnectedError when queue is full."""
    from datetime import datetime, timezone as tz
    client = _make_mock_client()
    stream = AsyncBinanceUserDataStream(
        client=client,
        ws_base_url="ws://unused",
        queue_size=1,
        overflow_policy="raise",
    )
    fill = BrokerFill(
        parent_id="c1", broker_order_id="1", client_order_id="c1", trade_id="t1",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        fee_asset="USDT", ts=datetime.now(tz.utc), is_maker=False,
    )
    fill2 = BrokerFill(
        parent_id="c2", broker_order_id="2", client_order_id="c2", trade_id="t2",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        fee_asset="USDT", ts=datetime.now(tz.utc), is_maker=False,
    )
    await stream._enqueue(fill)
    with pytest.raises(WSDisconnectedError):
        await stream._enqueue(fill2)


# ── conftest fixture ──────────────────────────────────────────────────────────

@pytest.fixture
def unused_tcp_port():
    """Find an available TCP port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
