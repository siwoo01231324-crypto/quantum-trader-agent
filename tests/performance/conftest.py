"""Shared fixtures for broker performance benchmarks.

Provides:
- respx mock router for REST endpoints (sync + async adapters share same URL)
- in-process fake WebSocket server via websockets.serve
- pre-built OrderRequest / BrokerFill factories
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
import respx
import websockets
import websockets.server

from src.brokers.base import OrderRequest, OrderType
from src.execution.base import Side, TimeInForce

# ── constants ────────────────────────────────────────────────────────────────

FAKE_BASE_URL = "https://testnet.binancefuture.com"
FAKE_WS_HOST = "127.0.0.1"
FAKE_WS_PORT = 19432  # unlikely to collide

# ── REST response templates ──────────────────────────────────────────────────

_ORDER_RESP: dict[str, Any] = {
    "orderId": 123456789,
    "clientOrderId": "x-bench-0001",
    "symbol": "BTCUSDT",
    "status": "FILLED",
    "origQty": "0.001",
    "executedQty": "0.001",
    "price": "0.00",
    "avgPrice": "65000.00",
    "side": "BUY",
    "positionSide": "LONG",
    "type": "MARKET",
    "updateTime": 1700000000000,
}

_POSITION_RESP: list[dict[str, Any]] = [
    {
        "symbol": "BTCUSDT",
        "positionSide": "LONG",
        "positionAmt": "0.001",
        "entryPrice": "65000.00",
        "markPrice": "65000.00",
        "unRealizedProfit": "0.00",
        "leverage": "1",
        "marginType": "isolated",
        "liquidationPrice": "0.00",
        "notional": "65.00",
    }
]

_BALANCE_RESP: list[dict[str, Any]] = [
    {
        "asset": "USDT",
        "balance": "10000.00",
        "availableBalance": "9935.00",
        "crossWalletBalance": "10000.00",
    }
]

_TIME_RESP: dict[str, Any] = {"serverTime": int(time.time() * 1000)}

_LISTEN_KEY_RESP: dict[str, Any] = {"listenKey": "bench-listen-key-0001"}


# ── shared respx mock router ─────────────────────────────────────────────────

@pytest.fixture()
def mock_rest() -> respx.MockRouter:
    """Active respx router wired to FAKE_BASE_URL for the duration of one test."""
    with respx.mock(base_url=FAKE_BASE_URL, assert_all_called=False) as router:
        # time sync (unsigned)
        router.get("/fapi/v1/time").respond(200, json=_TIME_RESP)
        # ping
        router.get("/fapi/v1/ping").respond(200, json={})
        # listen key
        router.post("/fapi/v1/listenKey").respond(200, json=_LISTEN_KEY_RESP)
        router.put("/fapi/v1/listenKey").respond(200, json={})
        router.delete("/fapi/v1/listenKey").respond(200, json={})
        # order
        router.post("/fapi/v1/order").respond(200, json=_ORDER_RESP)
        router.delete("/fapi/v1/order").respond(200, json={**_ORDER_RESP, "status": "CANCELED"})
        router.get("/fapi/v1/order").respond(200, json=_ORDER_RESP)
        # positions + balance
        router.get("/fapi/v2/positionRisk").respond(200, json=_POSITION_RESP)
        router.get("/fapi/v2/balance").respond(200, json=_BALANCE_RESP)
        # exchange info (for leverage / margin_type)
        router.post("/fapi/v1/leverage").respond(200, json={"leverage": 1, "symbol": "BTCUSDT"})
        router.post("/fapi/v1/marginType").respond(200, json={"code": 200, "msg": "success"})
        # position mode
        router.get("/fapi/v1/positionSide/dual").respond(200, json={"dualSidePosition": False})
        router.post("/fapi/v1/positionSide/dual").respond(200, json={"code": 200, "msg": "success"})
        yield router


# ── fake WebSocket server ────────────────────────────────────────────────────

def _make_fill_event(n: int) -> str:
    return json.dumps({
        "e": "ORDER_TRADE_UPDATE",
        "T": 1700000000000 + n,
        "o": {
            "s": "BTCUSDT",
            "c": f"x-bench-{n:06d}",
            "i": 123456789 + n,
            "t": n,
            "x": "TRADE",   # execution type — required by _parse_fill
            "X": "FILLED",
            "l": "0.001",
            "L": "65000.00",
            "n": "0.01",
            "N": "USDT",
            "m": False,
            "ps": "LONG",
        },
    })


async def _ws_handler(
    websocket: websockets.server.WebSocketServerProtocol,
    fill_count: int = 10,
    fill_interval_s: float = 0.001,
) -> None:
    """Send `fill_count` fill events then close cleanly."""
    for i in range(fill_count):
        await websocket.send(_make_fill_event(i))
        await asyncio.sleep(fill_interval_s)


@pytest_asyncio.fixture()
async def fake_ws_server() -> AsyncIterator[str]:
    """Start a local websockets server and yield ws:// URL."""
    async with websockets.serve(
        lambda ws: _ws_handler(ws, fill_count=50, fill_interval_s=0.001),
        FAKE_WS_HOST,
        FAKE_WS_PORT,
    ) as server:
        yield f"ws://{FAKE_WS_HOST}:{FAKE_WS_PORT}"


# ── order factory ─────────────────────────────────────────────────────────────

def make_order(symbol: str = "BTCUSDT", n: int = 0) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        side=Side.BUY,
        order_type=OrderType.MARKET,
        qty=Decimal("0.001"),
        price=None,
        tif=TimeInForce.GTC,
        reduce_only=False,
        emergency_exit=False,
        client_order_id=f"x-bench-{n:06d}",
    )
