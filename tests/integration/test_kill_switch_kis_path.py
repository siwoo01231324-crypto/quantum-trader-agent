"""Integration tests: kill-switch trip paths via KIS adapter mock (Stage 6.1, #105).

Three trigger × KIS adapter combinations:
  1. DrawdownTrigger — KIS balance polling sequence
  2. ApiErrorRateTrigger — KIS HTTP 5xx stream
  3. FillAnomalyTrigger — KIS WS fills burst

Each case verifies:
  - kill_switch.tripped is True
  - qta_kill_switch_state{reason} Gauge set to 1 (via helper that emits after trip)

No live network calls — all KIS adapter interactions are mocked.
"""
from __future__ import annotations

import time
from decimal import Decimal

import pytest
from prometheus_client import CollectorRegistry

from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch
from src.ops.triggers import ApiErrorRateTrigger, DrawdownTrigger, FillAnomalyTrigger


def _emit_kill_switch_state(metrics: Metrics, kill_switch: KillSwitch) -> None:
    """Emit qta_kill_switch_state Gauge after trip — mirrors what a monitoring loop would do."""
    if kill_switch.tripped:
        ev = kill_switch.last_event()
        reason = ev.reason if ev else "unknown"
        metrics.kill_switch_state.labels(reason=reason).set(1)


# ---------------------------------------------------------------------------
# 1. DrawdownTrigger — KIS balance polling
# ---------------------------------------------------------------------------

def test_drawdown_trigger_trips_on_kis_balance_series():
    """Mock KIS balance polling: peak=100k, then drops to 95k (-5%) → trip."""
    ks = KillSwitch()
    m = Metrics(registry=CollectorRegistry())
    trigger = DrawdownTrigger(kill=ks, limit=-0.03, starting_equity=100_000.0)

    # Simulate 5 KIS balance poll results (mocked — no real HTTP)
    mock_balances = [100_000.0, 100_500.0, 99_000.0, 97_000.0, 94_500.0]
    tripped = False
    for equity in mock_balances:
        if trigger.update(equity):
            tripped = True
            break

    assert tripped, "DrawdownTrigger should have tripped"
    assert ks.tripped

    _emit_kill_switch_state(m, ks)
    ev = ks.last_event()
    gauge_val = None
    for metric in m.kill_switch_state.collect():
        for s in metric.samples:
            if s.labels.get("reason") == ev.reason:
                gauge_val = s.value
    assert gauge_val == 1, "qta_kill_switch_state{reason} should be 1 after trip"


# ---------------------------------------------------------------------------
# 2. ApiErrorRateTrigger — KIS HTTP 5xx stream
# ---------------------------------------------------------------------------

def test_api_error_rate_trigger_trips_on_kis_5xx_stream():
    """Simulate 20 requests with 6 KIS HTTP 5xx errors → rate 30% > 5% threshold → trip."""
    ks = KillSwitch()
    m = Metrics(registry=CollectorRegistry())
    trigger = ApiErrorRateTrigger(
        kill=ks,
        window_seconds=300.0,
        error_rate_threshold=0.05,
        min_samples=20,
    )

    now = time.time()
    # 14 successes + 6 errors = 30% error rate, well above 5%
    events = [False] * 14 + [True] * 6
    tripped = False
    for i, is_error in enumerate(events):
        if trigger.record(is_error=is_error, ts=now + i * 0.1):
            tripped = True
            break

    assert tripped, "ApiErrorRateTrigger should have tripped on 30% KIS 5xx rate"
    assert ks.tripped

    _emit_kill_switch_state(m, ks)
    ev = ks.last_event()
    gauge_val = None
    for metric in m.kill_switch_state.collect():
        for s in metric.samples:
            if s.labels.get("reason") == ev.reason:
                gauge_val = s.value
    assert gauge_val == 1


# ---------------------------------------------------------------------------
# 3. FillAnomalyTrigger — KIS WS fills burst (5 fills in 1 second)
# ---------------------------------------------------------------------------

def test_fill_anomaly_trigger_trips_on_kis_ws_fill_burst():
    """5 KIS WS fills for same symbol within 1 second → trip."""
    ks = KillSwitch()
    m = Metrics(registry=CollectorRegistry())
    trigger = FillAnomalyTrigger(kill=ks, window_seconds=1.0, burst_threshold=5)

    now = time.time()
    symbol = "005930"
    tripped = False
    for i in range(5):
        if trigger.record_fill(symbol, ts=now + i * 0.1):
            tripped = True
            break

    assert tripped, "FillAnomalyTrigger should have tripped on 5 KIS fills in 1s"
    assert ks.tripped

    _emit_kill_switch_state(m, ks)
    ev = ks.last_event()
    gauge_val = None
    for metric in m.kill_switch_state.collect():
        for s in metric.samples:
            if s.labels.get("reason") == ev.reason:
                gauge_val = s.value
    assert gauge_val == 1


# ---------------------------------------------------------------------------
# 4. AsyncOrderRouter.health_check DOWN → kills switch (T5 integration)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_router_health_check_down_trips_kill_switch():
    """AsyncOrderRouter.health_check() returns DOWN → kill_switch trips (T5 path)."""
    from unittest.mock import AsyncMock, MagicMock
    from src.brokers.async_router import AsyncOrderRouter
    from src.brokers.base import AsyncBrokerAdapter, HealthStatus

    broker = MagicMock(spec=AsyncBrokerAdapter)
    broker.name = "kis_paper"
    broker.paper = True
    broker.health_check = AsyncMock(return_value=HealthStatus.DOWN)

    ks = KillSwitch()
    m = Metrics(registry=CollectorRegistry())
    router = AsyncOrderRouter(active=broker, kill_switch=ks, metrics=m)

    status = await router.health_check()

    assert status == HealthStatus.DOWN
    assert ks.tripped

    _emit_kill_switch_state(m, ks)
    ev = ks.last_event()
    assert ev.reason == "broker_unhealthy"

    gauge_val = None
    for metric in m.kill_switch_state.collect():
        for s in metric.samples:
            if s.labels.get("reason") == ev.reason:
                gauge_val = s.value
    assert gauge_val == 1
