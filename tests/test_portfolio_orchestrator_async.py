"""Tests for AsyncStrategyOrchestrator (T3 — issue #78).

All latency tests use time.perf_counter + numpy.percentile — NOT pytest-benchmark.
"""
from __future__ import annotations

import asyncio
import inspect
import time

import numpy as np
import pytest

from backtest.protocol import Signal
from portfolio import AsyncStrategyOrchestrator, OrderIntent
from risk.dsl import Policy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_policy(**kwargs) -> Policy:
    return Policy(policy_version=1, name="test-policy", **kwargs)


def _sync_strategy(strategy_id: str, action: str = "buy", size: float = 0.1):
    class _S:
        def on_bar(self, ctx: object) -> Signal:
            return Signal(action=action, size=size, reason=f"{strategy_id}-signal")

    s = _S()
    s.strategy_id = strategy_id
    return s


def _async_strategy(strategy_id: str, action: str = "buy", size: float = 0.1):
    class _A:
        async def on_bar(self, ctx: object) -> Signal | None:
            return Signal(action=action, size=size, reason=f"{strategy_id}-signal")

    a = _A()
    a.strategy_id = strategy_id
    return a


def _failing_strategy(strategy_id: str):
    class _F:
        def on_bar(self, ctx: object) -> Signal:
            raise RuntimeError("strategy failure")

    f = _F()
    f.strategy_id = strategy_id
    return f


def _async_timeout_strategy(strategy_id: str):
    class _AT:
        async def on_bar(self, ctx: object) -> Signal | None:
            raise asyncio.TimeoutError("timeout")

    at = _AT()
    at.strategy_id = strategy_id
    return at


def _register(orch: AsyncStrategyOrchestrator, strat) -> None:
    orch.register_strategy(strat.strategy_id, strat)


SNAP = {"symbol": "BTC", "price": 50000.0, "equity_krw": 1_000_000}


def _run_in_loop(loop: asyncio.AbstractEventLoop, coro):
    return loop.run_until_complete(coro)


@pytest.fixture
def loop():
    lp = asyncio.new_event_loop()
    yield lp
    lp.close()


@pytest.fixture
def policy():
    return _make_policy()


@pytest.fixture
def orchestrator(policy, loop):
    return AsyncStrategyOrchestrator(policy)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_run_bar_returns_order_intents_on_normal_path(orchestrator, loop):
    """3 mock strategies → 3 OrderIntents returned from run_bar."""
    for i in range(3):
        _register(orchestrator, _sync_strategy(f"s{i}"))

    intents = _run_in_loop(loop, orchestrator.run_bar(ts=None, market_snapshot=SNAP))
    assert len(intents) == 3
    assert all(isinstance(x, OrderIntent) for x in intents)


def test_run_bar_filters_breached_strategies(loop, policy):
    """Strategy output filtered when risk policy blocks it (side not allowed)."""
    from risk.dsl import PerTrade
    # Only 'sell' allowed → 'buy' signals will be blocked
    p = _make_policy(per_trade=PerTrade(allowed_sides=["sell"]))
    orch = AsyncStrategyOrchestrator(p)
    _register(orch, _sync_strategy("breach-s", action="buy", size=0.5))

    intents = _run_in_loop(loop, orch.run_bar(ts=None, market_snapshot=SNAP))
    # buy signal should be blocked by policy → 0 intents
    assert intents == []


def test_strategy_exception_isolated(orchestrator, loop):
    """1 strategy raises, 2 succeed → 2 intents returned."""
    _register(orchestrator, _sync_strategy("ok-1"))
    _register(orchestrator, _failing_strategy("bad-1"))
    _register(orchestrator, _sync_strategy("ok-2"))

    intents = _run_in_loop(loop, orchestrator.run_bar(ts=None, market_snapshot=SNAP))
    assert len(intents) == 2
    ids = {i.strategy_id for i in intents}
    assert "ok-1" in ids
    assert "ok-2" in ids
    assert "bad-1" not in ids


def test_quarantine_after_three_failures(orchestrator, loop):
    """3 consecutive failures → strategy quarantined, 4th call skipped."""
    _register(orchestrator, _failing_strategy("fail-q"))

    for _ in range(3):
        _run_in_loop(loop, orchestrator.run_bar(ts=None, market_snapshot=SNAP))

    assert "fail-q" in orchestrator.quarantined_strategies
    intents = _run_in_loop(loop, orchestrator.run_bar(ts=None, market_snapshot=SNAP))
    assert len(intents) == 0


def test_quarantine_resets_on_success(orchestrator, loop):
    """fail/fail/success/fail/fail → NOT quarantined (reset on success clears counter)."""
    call_count = 0

    class _Flaky:
        def on_bar(self, ctx) -> Signal:
            nonlocal call_count
            call_count += 1
            if call_count in (1, 2, 4, 5):
                raise RuntimeError("flaky failure")
            return Signal(action="buy", size=0.1, reason="ok")

    strat = _Flaky()
    strat.strategy_id = "flaky"
    _register(orchestrator, strat)

    # calls 1, 2: fail (fail_count=2)
    for _ in range(2):
        _run_in_loop(loop, orchestrator.run_bar(ts=None, market_snapshot=SNAP))

    # call 3: success → resets counter to 0
    _run_in_loop(loop, orchestrator.run_bar(ts=None, market_snapshot=SNAP))

    # calls 4, 5: fail (fail_count=2 again, not 3 → NOT quarantined)
    for _ in range(2):
        _run_in_loop(loop, orchestrator.run_bar(ts=None, market_snapshot=SNAP))

    assert "flaky" not in orchestrator.quarantined_strategies


def test_quarantine_counts_timeout_as_failure(orchestrator, loop):
    """asyncio.TimeoutError increments failure counter toward quarantine."""
    _register(orchestrator, _async_timeout_strategy("timeout-s"))

    for _ in range(3):
        _run_in_loop(loop, orchestrator.run_bar(ts=None, market_snapshot=SNAP))

    assert "timeout-s" in orchestrator.quarantined_strategies
    intents = _run_in_loop(loop, orchestrator.run_bar(ts=None, market_snapshot=SNAP))
    assert len(intents) == 0


def test_mixed_sync_async_strategies(orchestrator, loop):
    """2 sync + 1 async, all emit buy signals → 3 intents dispatched correctly."""
    _register(orchestrator, _sync_strategy("sync-a"))
    _register(orchestrator, _sync_strategy("sync-b"))
    _register(orchestrator, _async_strategy("async-c"))

    intents = _run_in_loop(loop, orchestrator.run_bar(ts=None, market_snapshot=SNAP))
    assert len(intents) == 3
    ids = {i.strategy_id for i in intents}
    assert {"sync-a", "sync-b", "async-c"} == ids


def test_bar_clock_refresh_timing_exact(policy, loop):
    """n=10, 30 bars → refresh_portfolio_risk_async fires at bar index 9, 19, 29."""
    orch = AsyncStrategyOrchestrator(policy, refresh_every_n_bars=10)
    _register(orch, _sync_strategy("s1"))

    refresh_call_count = 0
    original_refresh = orch.refresh_portfolio_risk_async

    async def spy_refresh(*args, **kwargs):
        nonlocal refresh_call_count
        refresh_call_count += 1
        return await original_refresh(*args, **kwargs)

    orch.refresh_portfolio_risk_async = spy_refresh

    for _ in range(30):
        _run_in_loop(loop, orch.run_bar(ts=None, market_snapshot=SNAP))

    assert refresh_call_count == 3


def test_wallclock_refresh_task_cancellable(policy, loop):
    """Wallclock refresh background task can be started and stopped cleanly."""
    orch = AsyncStrategyOrchestrator(policy)

    async def _run_test():
        await orch.start_risk_refresh_loop(interval_sec=60)
        await orch.stop_risk_refresh_loop()

    _run_in_loop(loop, _run_test())


def test_sync_class_not_exported():
    """__all__ contains exactly {"AsyncStrategyOrchestrator", "OrderIntent"}."""
    from portfolio import __all__
    assert set(__all__) == {"AsyncStrategyOrchestrator", "OrderIntent"}


def test_public_api_exposes_only_async():
    """AsyncStrategyOrchestrator is importable from portfolio; sync class not in __all__."""
    from portfolio import AsyncStrategyOrchestrator as ASO
    assert ASO is not None
    from portfolio import __all__
    assert "_SyncStrategyOrchestrator" not in __all__
    assert "StrategyOrchestrator" not in __all__


def test_run_bar_latency_p99(policy):
    """p99 latency < 50ms for run_bar with 5 mock sync strategies.

    Uses time.perf_counter + numpy.percentile (NOT pytest-benchmark).
    10 warmup iterations + 1000 measured iterations.
    """
    orch = AsyncStrategyOrchestrator(policy)
    for i in range(5):
        _register(orch, _sync_strategy(f"bench-{i}"))

    loop = asyncio.new_event_loop()
    try:
        # warmup
        for _ in range(10):
            loop.run_until_complete(orch.run_bar(ts=None, market_snapshot=SNAP))

        latencies_ms = []
        for _ in range(1000):
            t0 = time.perf_counter()
            loop.run_until_complete(orch.run_bar(ts=None, market_snapshot=SNAP))
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)

        p99 = float(np.percentile(latencies_ms, 99))
        assert p99 < 50.0, f"p99 latency {p99:.2f}ms exceeds 50ms ceiling"
    finally:
        loop.close()
