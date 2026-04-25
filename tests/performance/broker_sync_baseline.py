"""C8a — Sync broker adapter baseline benchmark.

Runs BinanceFuturesAdapter (sync) via ThreadPoolExecutor(50) to simulate
concurrent usage. Results are written to tests/performance/results_sync.json
for comparison by the async bench (C8b).

Run:
    pytest tests/performance/broker_sync_baseline.py -v --tb=short

Pass/fail gates (recorded, not enforced here — enforced in broker_async_bench.py):
    - throughput recorded in req/s
    - p95 latency recorded in ms
"""
from __future__ import annotations

import concurrent.futures
import json
import statistics
import time
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
import responses as responses_lib

from src.brokers.binance.adapter import BinanceFuturesAdapter
from src.brokers.base import OrderRequest, OrderType
from src.execution.base import Side, TimeInForce

# ── constants ────────────────────────────────────────────────────────────────

FAKE_BASE_URL = "http://testnet-sync.binancefuture.com"
RESULTS_PATH = Path(__file__).parent / "results_sync.json"

_CONCURRENCY = 50
_REQUESTS_PER_WORKER = 4   # total = 200 requests


# ── response fixtures ────────────────────────────────────────────────────────

_ORDER_RESP: dict[str, Any] = {
    "orderId": 123456789,
    "clientOrderId": "x-sync-0001",
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
_EXCHANGE_INFO_RESP: dict[str, Any] = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "filters": [
                {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                {"filterType": "MARKET_LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
            ],
        }
    ]
}


def _make_adapter() -> BinanceFuturesAdapter:
    return BinanceFuturesAdapter(
        api_key="bench-key",
        secret="bench-secret",
        base_url=FAKE_BASE_URL,
        paper=True,
    )


def _one_request_cycle(adapter: BinanceFuturesAdapter, n: int) -> float:
    """Execute place_order + get_positions in one call and return wall-time (s)."""
    order = OrderRequest(
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        qty=Decimal("0.001"),
        price=None,
        tif=TimeInForce.GTC,
        reduce_only=False,
        emergency_exit=False,
        client_order_id=f"x-sync-{n:06d}",
    )
    t0 = time.perf_counter()
    adapter.place_order(order)
    adapter.get_positions("BTCUSDT")
    return time.perf_counter() - t0


# ── benchmark scenarios ──────────────────────────────────────────────────────

@responses_lib.activate
def _run_scenario_rest_throughput() -> dict[str, float]:
    """Scenario 1: N=200 place_order calls via ThreadPoolExecutor(50).

    Measures raw REST throughput with mocked network (zero real I/O).
    """
    # Register responses (responses lib intercepts requests globally)
    responses_lib.add(responses_lib.GET, f"{FAKE_BASE_URL}/fapi/v1/time", json=_TIME_RESP)
    responses_lib.add(responses_lib.GET, f"{FAKE_BASE_URL}/fapi/v1/exchangeInfo", json=_EXCHANGE_INFO_RESP)
    # Allow unlimited repeats for order + position
    for _ in range(_CONCURRENCY * _REQUESTS_PER_WORKER + 10):
        responses_lib.add(responses_lib.POST, f"{FAKE_BASE_URL}/fapi/v1/order", json=_ORDER_RESP)
        responses_lib.add(responses_lib.GET, f"{FAKE_BASE_URL}/fapi/v2/positionRisk", json=_POSITION_RESP)
        responses_lib.add(responses_lib.GET, f"{FAKE_BASE_URL}/fapi/v2/balance", json=_BALANCE_RESP)

    latencies: list[float] = []

    def worker(worker_id: int) -> list[float]:
        adapter = _make_adapter()
        times = []
        for i in range(_REQUESTS_PER_WORKER):
            elapsed = _one_request_cycle(adapter, worker_id * _REQUESTS_PER_WORKER + i)
            times.append(elapsed)
        return times

    t_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=_CONCURRENCY) as pool:
        futures = [pool.submit(worker, wid) for wid in range(_CONCURRENCY)]
        for fut in concurrent.futures.as_completed(futures):
            latencies.extend(fut.result())
    t_end = time.perf_counter()

    total_s = t_end - t_start
    n_total = len(latencies)
    req_per_s = n_total / total_s
    latencies_ms = [x * 1000 for x in latencies]
    p95_ms = statistics.quantiles(latencies_ms, n=100)[94]

    return {
        "scenario": "rest_throughput_sync",
        "n_requests": n_total,
        "total_s": round(total_s, 4),
        "req_per_s": round(req_per_s, 2),
        "p50_ms": round(statistics.median(latencies_ms), 3),
        "p95_ms": round(p95_ms, 3),
        "p99_ms": round(statistics.quantiles(latencies_ms, n=100)[98], 3),
    }


# ── pytest entry points ──────────────────────────────────────────────────────

def test_sync_baseline_rest_throughput() -> None:
    """Run sync REST throughput baseline and persist results."""
    result = _run_scenario_rest_throughput()

    print(f"\n[SYNC BASELINE] req/s={result['req_per_s']}  "
          f"p50={result['p50_ms']:.1f}ms  p95={result['p95_ms']:.1f}ms  "
          f"p99={result['p99_ms']:.1f}ms  total={result['total_s']:.2f}s")

    # Persist for async bench comparison
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if RESULTS_PATH.exists():
        try:
            data = json.loads(RESULTS_PATH.read_text())
        except Exception:
            data = {}
    data["sync"] = result
    RESULTS_PATH.write_text(json.dumps(data, indent=2))

    # Sanity: at least 1 req/s in a mocked environment
    assert result["req_per_s"] > 1.0, f"Baseline throughput suspiciously low: {result['req_per_s']}"
    assert result["p95_ms"] < 10_000.0, "p95 must be < 10s even for sync"
