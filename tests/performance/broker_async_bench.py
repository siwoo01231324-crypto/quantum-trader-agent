"""C8b — Async broker adapter benchmark (4 scenarios).

Enforces numeric pass/fail gates from the plan:
    - async req/s >= sync req/s * 3.0   (Scenario 1)
    - async p95   <= sync p95  * 1.1    (Scenario 1)
    - fill loss + dup == 0              (Scenario 2)
    - reconnect wall-time <= 10s        (Scenario 3)
    - fill gap after reconnect == 0     (Scenario 3)

Run:
    pytest tests/performance/broker_async_bench.py -v --tb=short

Requires broker_sync_baseline.py to have run first (reads results_sync.json).
If baseline is missing, Scenario 1 gates are skipped with a warning.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import respx
import websockets
import websockets.server

from src.brokers.base import OrderRequest, OrderType
from src.execution.base import Side, TimeInForce
from src.brokers.binance.async_adapter import AsyncBinanceFuturesAdapter
from src.brokers.binance.async_ws import AsyncBinanceUserDataStream

# ── constants ────────────────────────────────────────────────────────────────

FAKE_BASE_URL = "https://testnet-async.binancefuture.com"
FAKE_WS_HOST = "127.0.0.1"
FAKE_WS_PORT = 19433  # separate port from conftest to avoid collision
RESULTS_PATH = Path(__file__).parent / "results_sync.json"
RESULTS_ASYNC_PATH = Path(__file__).parent / "results_async.json"

_CONCURRENCY = 50
_REQUESTS_PER_WORKER = 4   # total = 200 tasks (matches sync baseline)
_FILL_COUNT = 200           # fills for Scenario 2
_RECONNECT_TIMEOUT_S = 10.0

# ── REST response templates ──────────────────────────────────────────────────

_ORDER_RESP: dict[str, Any] = {
    "orderId": 999000000,
    "clientOrderId": "x-async-0001",
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
_LISTEN_KEY_RESP: dict[str, Any] = {"listenKey": "async-bench-listen-key"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_fill_event(n: int) -> str:
    return json.dumps({
        "e": "ORDER_TRADE_UPDATE",
        "T": 1700000000000 + n,
        "o": {
            "s": "BTCUSDT",
            "c": f"x-async-{n:06d}",
            "i": 999000000 + n,
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


def _mock_rest_router(base_url: str) -> respx.MockRouter:
    router = respx.MockRouter(base_url=base_url, assert_all_called=False)
    router.get("/fapi/v1/time").respond(200, json=_TIME_RESP)
    router.get("/fapi/v1/ping").respond(200, json={})
    router.post("/fapi/v1/listenKey").respond(200, json=_LISTEN_KEY_RESP)
    router.put("/fapi/v1/listenKey").respond(200, json={})
    router.delete("/fapi/v1/listenKey").respond(200, json={})
    router.post("/fapi/v1/order").respond(200, json=_ORDER_RESP)
    router.delete("/fapi/v1/order").respond(200, json={**_ORDER_RESP, "status": "CANCELED"})
    router.get("/fapi/v1/order").respond(200, json=_ORDER_RESP)
    router.get("/fapi/v2/positionRisk").respond(200, json=_POSITION_RESP)
    router.get("/fapi/v2/balance").respond(200, json=_BALANCE_RESP)
    router.post("/fapi/v1/leverage").respond(200, json={"leverage": 1, "symbol": "BTCUSDT"})
    router.post("/fapi/v1/marginType").respond(200, json={"code": 200, "msg": "success"})
    router.get("/fapi/v1/positionSide/dual").respond(200, json={"dualSidePosition": False})
    router.post("/fapi/v1/positionSide/dual").respond(200, json={"code": 200, "msg": "success"})
    return router


def _make_order(n: int) -> OrderRequest:
    return OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        qty=Decimal("0.001"),
        price=None,
        tif=TimeInForce.GTC,
        reduce_only=False,
        emergency_exit=False,
        client_order_id=f"x-async-{n:06d}",
    )


def _load_sync_results() -> dict[str, Any] | None:
    if RESULTS_PATH.exists():
        try:
            data = json.loads(RESULTS_PATH.read_text())
            return data.get("sync")
        except Exception:
            return None
    return None


# ── Scenario 1: REST throughput (async vs sync) ───────────────────────────────

@pytest.mark.asyncio
async def test_scenario1_rest_throughput() -> None:
    """200 concurrent place_order + get_positions via asyncio.TaskGroup.

    Gate: async req/s >= sync * 3.0, async p95 <= sync_p95 * 1.1.
    """
    router = _mock_rest_router(FAKE_BASE_URL)

    async def one_cycle(adapter: AsyncBinanceFuturesAdapter, n: int) -> float:
        order = _make_order(n)
        t0 = time.perf_counter()
        await adapter.place_order(order)
        await adapter.get_positions("BTCUSDT")
        return time.perf_counter() - t0

    latencies: list[float] = []

    with router:
        adapter = AsyncBinanceFuturesAdapter(
            api_key="bench-key",
            secret="bench-secret",
            base_url=FAKE_BASE_URL,
            paper=True,
        )
        t_start = time.perf_counter()
        total = _CONCURRENCY * _REQUESTS_PER_WORKER
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(one_cycle(adapter, i)) for i in range(total)]
        latencies = [t.result() for t in tasks]
        t_end = time.perf_counter()
        await adapter.aclose()

    total_s = t_end - t_start
    n_total = len(latencies)
    req_per_s = n_total / total_s
    latencies_ms = [x * 1000 for x in latencies]
    p95_ms = statistics.quantiles(latencies_ms, n=100)[94]

    async_result = {
        "scenario": "rest_throughput_async",
        "n_requests": n_total,
        "total_s": round(total_s, 4),
        "req_per_s": round(req_per_s, 2),
        "p50_ms": round(statistics.median(latencies_ms), 3),
        "p95_ms": round(p95_ms, 3),
        "p99_ms": round(statistics.quantiles(latencies_ms, n=100)[98], 3),
    }

    print(f"\n[ASYNC Scenario 1] req/s={async_result['req_per_s']}  "
          f"p50={async_result['p50_ms']:.1f}ms  p95={async_result['p95_ms']:.1f}ms  "
          f"p99={async_result['p99_ms']:.1f}ms  total={async_result['total_s']:.2f}s")

    # Persist
    RESULTS_ASYNC_PATH.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if RESULTS_ASYNC_PATH.exists():
        try:
            data = json.loads(RESULTS_ASYNC_PATH.read_text())
        except Exception:
            data = {}
    data["scenario1"] = async_result
    RESULTS_ASYNC_PATH.write_text(json.dumps(data, indent=2))

    # Gate vs sync baseline
    sync = _load_sync_results()
    if sync is None:
        pytest.skip("Sync baseline not found — run broker_sync_baseline.py first")

    sync_req_s = sync["req_per_s"]
    sync_p95 = sync["p95_ms"]
    gate_throughput = sync_req_s * 3.0
    gate_p95 = sync_p95 * 1.1

    print(f"  [GATE] req/s >= {gate_throughput:.1f} (sync*3): {'PASS' if req_per_s >= gate_throughput else 'FAIL'}")
    print(f"  [GATE] p95 <= {gate_p95:.1f}ms (sync_p95*1.1): {'PASS' if p95_ms <= gate_p95 else 'FAIL'}")

    assert req_per_s >= gate_throughput, (
        f"Async throughput {req_per_s:.1f} req/s < sync*3.0 ({gate_throughput:.1f} req/s)"
    )
    assert p95_ms <= gate_p95, (
        f"Async p95 {p95_ms:.1f}ms > sync_p95*1.1 ({gate_p95:.1f}ms)"
    )


# ── Scenario 2: Fill integrity (zero loss + zero dup) ────────────────────────

@pytest.mark.asyncio
async def test_scenario2_fill_integrity() -> None:
    """stream_fills yields exactly N fills, no loss, no duplicates.

    Gate: fill_loss == 0, fill_dup == 0.
    """
    fill_events = [_make_fill_event(i) for i in range(_FILL_COUNT)]
    received: list[str] = []  # trade_ids

    async def _ws_handler(ws: websockets.server.WebSocketServerProtocol) -> None:
        for evt in fill_events:
            await ws.send(evt)
        # Keep open long enough for reader to drain
        await asyncio.sleep(2.0)

    async with websockets.serve(_ws_handler, FAKE_WS_HOST, FAKE_WS_PORT):
        ws_url = f"ws://{FAKE_WS_HOST}:{FAKE_WS_PORT}/stream-s2"

        router = _mock_rest_router(FAKE_BASE_URL)
        with router:
            adapter = AsyncBinanceFuturesAdapter(
                api_key="bench-key",
                secret="bench-secret",
                base_url=FAKE_BASE_URL,
                ws_base_url=ws_url,
                paper=True,
                fill_queue_size=_FILL_COUNT + 100,
                overflow_policy="block",
            )

            # Patch listen_key.issue() to avoid REST call for listenKey in ws
            with patch.object(
                adapter._client, "issue_listen_key", new=AsyncMock(return_value="bench-key-s2")
            ), patch.object(
                adapter._client, "delete_listen_key", new=AsyncMock(return_value=None)
            ), patch.object(
                adapter._client, "extend_listen_key", new=AsyncMock(return_value=None)
            ):
                try:
                    async with asyncio.timeout(_FILL_COUNT * 0.05 + 5.0):
                        async for fill in adapter.stream_fills():
                            received.append(fill.trade_id)
                            if len(received) >= _FILL_COUNT:
                                break
                finally:
                    await adapter.aclose()

    fill_loss = _FILL_COUNT - len(received)
    fill_dup = len(received) - len(set(received))

    print(f"\n[ASYNC Scenario 2] fills_expected={_FILL_COUNT}  "
          f"received={len(received)}  loss={fill_loss}  dup={fill_dup}")

    assert fill_loss == 0, f"Fill loss detected: {fill_loss} fills missing"
    assert fill_dup == 0, f"Fill duplicates detected: {fill_dup} dups"


# ── Scenario 3: Reconnect wall-time ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_scenario3_reconnect_latency() -> None:
    """After abrupt WS disconnect, stream resumes within 10s.

    Gate: reconnect_wall_time <= 10s, fill_gap == 0.
    """
    _RECONNECT_PORT = FAKE_WS_PORT + 1
    reconnect_times: list[float] = []
    fills_received: list[int] = []
    connection_count = 0

    async def _ws_handler_with_disconnect(ws: websockets.server.WebSocketServerProtocol) -> None:
        nonlocal connection_count
        connection_count += 1
        conn_n = connection_count

        if conn_n == 1:
            # First connection: send 5 fills then close to trigger reconnect
            for i in range(5):
                await ws.send(_make_fill_event(i))
                await asyncio.sleep(0.01)
            # Close with code 1011 (server error) to trigger reconnect path
            await ws.close(code=1011, reason="simulated disconnect")
        else:
            # Second connection: send 5 more fills
            for i in range(5, 10):
                await ws.send(_make_fill_event(i))
                await asyncio.sleep(0.01)
            await asyncio.sleep(2.0)

    t_disconnect = 0.0

    async with websockets.serve(_ws_handler_with_disconnect, FAKE_WS_HOST, _RECONNECT_PORT):
        ws_url = f"ws://{FAKE_WS_HOST}:{_RECONNECT_PORT}/stream-s3"

        router = _mock_rest_router(FAKE_BASE_URL)
        with router:
            adapter = AsyncBinanceFuturesAdapter(
                api_key="bench-key",
                secret="bench-secret",
                base_url=FAKE_BASE_URL,
                ws_base_url=ws_url,
                paper=True,
                fill_queue_size=100,
                overflow_policy="block",
            )

            with patch.object(
                adapter._client, "issue_listen_key", new=AsyncMock(return_value="bench-key-s3")
            ), patch.object(
                adapter._client, "delete_listen_key", new=AsyncMock(return_value=None)
            ), patch.object(
                adapter._client, "extend_listen_key", new=AsyncMock(return_value=None)
            ):
                t_start = time.perf_counter()
                try:
                    async with asyncio.timeout(30.0):
                        async for fill in adapter.stream_fills():
                            fills_received.append(int(fill.trade_id))
                            if len(fills_received) == 5:
                                # Record time of disconnect
                                t_disconnect = time.perf_counter()
                            if len(fills_received) == 10:
                                break
                finally:
                    await adapter.aclose()

    t_reconnect_fill = time.perf_counter()
    reconnect_wall = t_reconnect_fill - t_disconnect if t_disconnect > 0 else 0.0

    fills_received.sort()
    expected = list(range(10))
    fill_gap = len(set(expected) - set(fills_received))

    print(f"\n[ASYNC Scenario 3] fills={len(fills_received)}  "
          f"reconnect_wall={reconnect_wall:.2f}s  fill_gap={fill_gap}")

    assert reconnect_wall <= _RECONNECT_TIMEOUT_S, (
        f"Reconnect took {reconnect_wall:.2f}s > {_RECONNECT_TIMEOUT_S}s"
    )
    assert fill_gap == 0, f"Fill gap after reconnect: {fill_gap} fills missing"


# ── Scenario 4: Concurrent symbol monitoring ─────────────────────────────────

@pytest.mark.asyncio
async def test_scenario4_concurrent_symbol_monitoring() -> None:
    """50 concurrent get_positions calls (simulating 50-symbol monitoring).

    Gate: all complete without error, total wall-time measured.
    """
    _N_SYMBOLS = 50

    router = _mock_rest_router(FAKE_BASE_URL)

    with router:
        adapter = AsyncBinanceFuturesAdapter(
            api_key="bench-key",
            secret="bench-secret",
            base_url=FAKE_BASE_URL,
            paper=True,
        )

        symbols = [f"SYM{i:03d}USDT" for i in range(_N_SYMBOLS)]

        t_start = time.perf_counter()
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(adapter.get_positions(sym)) for sym in symbols]
        results = [t.result() for t in tasks]
        t_end = time.perf_counter()
        await adapter.aclose()

    wall_s = t_end - t_start
    errors = sum(1 for r in results if r is None)

    print(f"\n[ASYNC Scenario 4] symbols={_N_SYMBOLS}  "
          f"wall={wall_s:.3f}s  errors={errors}")

    assert errors == 0, f"{errors} symbol monitoring calls returned None"
    assert wall_s < 30.0, f"50-symbol monitoring took {wall_s:.2f}s > 30s"


# ── Summary test: print comparison table ────────────────────────────────────

def test_bench_summary() -> None:
    """Print sync vs async comparison. Always passes — informational only."""
    sync = _load_sync_results()
    async_data: dict[str, Any] = {}
    if RESULTS_ASYNC_PATH.exists():
        try:
            async_data = json.loads(RESULTS_ASYNC_PATH.read_text())
        except Exception:
            pass

    s1 = async_data.get("scenario1", {})

    print("\n" + "=" * 60)
    print("BROKER ADAPTER BENCHMARK SUMMARY")
    print("=" * 60)
    if sync and s1:
        ratio = s1.get("req_per_s", 0) / sync.get("req_per_s", 1)
        print(f"{'Metric':<25} {'Sync':>12} {'Async':>12} {'Ratio':>8}")
        print("-" * 60)
        print(f"{'req/s':<25} {sync.get('req_per_s', 0):>12.1f} {s1.get('req_per_s', 0):>12.1f} {ratio:>7.1f}x")
        print(f"{'p50 (ms)':<25} {sync.get('p50_ms', 0):>12.1f} {s1.get('p50_ms', 0):>12.1f}")
        print(f"{'p95 (ms)':<25} {sync.get('p95_ms', 0):>12.1f} {s1.get('p95_ms', 0):>12.1f}")
        print(f"{'p99 (ms)':<25} {sync.get('p99_ms', 0):>12.1f} {s1.get('p99_ms', 0):>12.1f}")
        print(f"{'total_s':<25} {sync.get('total_s', 0):>12.2f} {s1.get('total_s', 0):>12.2f}")
        print("-" * 60)
        print(f"Gate: async req/s >= sync*3.0 = {sync.get('req_per_s', 0)*3:.1f}  -> {'PASS' if s1.get('req_per_s',0) >= sync.get('req_per_s',0)*3.0 else 'FAIL'}")
        print(f"Gate: async p95  <= sync*1.1  = {sync.get('p95_ms', 0)*1.1:.1f}ms -> {'PASS' if s1.get('p95_ms',0) <= sync.get('p95_ms',0)*1.1 else 'FAIL'}")
    else:
        print("(No baseline data — run broker_sync_baseline.py then broker_async_bench.py)")
    print("=" * 60)
