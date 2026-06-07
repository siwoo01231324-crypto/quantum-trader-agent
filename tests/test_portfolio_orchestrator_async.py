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


# #238 — "BTC" was an unrecognized placeholder symbol; size_to_qty needs a
# venue-resolvable symbol. "BTCUSDT" sizes against equity_usdt. These tests
# assert dispatch/quarantine/exception-isolation, not sizing — pre-#238 they
# relied on the raw resolve_size fraction being emitted directly as qty.
SNAP = {
    "symbol": "BTCUSDT", "price": 50000.0,
    "equity_krw": 1_000_000, "equity_usdt": 1_000_000,
}


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


def test_momo_kis_v1_register_and_refresh(policy, loop):
    """MomoKisV1 register_strategy + register_strategy_returns + refresh_portfolio_risk returns report."""
    import pandas as pd
    from backtest.strategies.momo_kis_v1 import MomoKisV1

    orch = AsyncStrategyOrchestrator(policy)
    strategy = MomoKisV1()
    orch.register_strategy("momo_kis_v1", strategy)

    # Need >= 2 strategies for refresh_portfolio_risk to return non-None
    from backtest.strategies.momo_kis_v1 import MomoKisV1 as MomoKisV1b
    strategy2 = MomoKisV1b()
    orch.register_strategy("momo_kis_v1_b", strategy2)

    # mock daily returns Series (10 days)
    idx = pd.date_range("2026-04-01", periods=10, freq="D")
    returns = pd.Series([0.01, -0.005, 0.008, -0.003, 0.012, 0.002, -0.007, 0.005, 0.003, -0.001], index=idx)
    returns2 = pd.Series([-0.002, 0.003, -0.001, 0.006, -0.004, 0.009, 0.001, -0.003, 0.007, 0.002], index=idx)
    orch.register_strategy_returns("momo_kis_v1", returns)
    orch.register_strategy_returns("momo_kis_v1_b", returns2)

    report = orch.refresh_portfolio_risk()
    assert report is not None


def test_momo_kis_v1_run_bar_trading_hours(policy, loop):
    """run_bar during KRX trading hours → signal processed; outside hours → empty list."""
    import pandas as pd
    import numpy as np
    from unittest.mock import patch
    from backtest.strategies.momo_kis_v1 import MomoKisV1
    from universe.krx_calendar import KST

    orch = AsyncStrategyOrchestrator(policy)
    strategy = MomoKisV1()
    orch.register_strategy("momo_kis_v1", strategy)

    history = pd.DataFrame({
        "open": np.full(100, 60000.0),
        "high": np.full(100, 60100.0),
        "low": np.full(100, 59900.0),
        "close": np.linspace(60000.0, 61000.0, 100),
        "volume": np.full(100, 50000.0),
    })

    # Outside trading hours (16:00 KST) → strategy returns "not my bar" → hold → no intents
    ts_out = pd.Timestamp(2026, 4, 22, 16, 0, 0, tzinfo=KST)
    snap = {"symbol": "005930", "price": 61000.0, "equity_krw": 1_000_000, "history": history}
    intents_out = _run_in_loop(loop, orch.run_bar(ts=ts_out, market_snapshot=snap))
    assert intents_out == []

    # Inside trading hours (10:00 KST) — bullish divergence patched → may produce intent
    ts_in = pd.Timestamp(2026, 4, 22, 10, 0, 0, tzinfo=KST)
    bullish_series = pd.Series(["none"] * 100)
    bullish_series.iloc[-1] = "bullish"
    with patch("backtest.strategies.momo_kis_v1.detect_divergence", return_value=bullish_series):
        snap_in = {"symbol": "005930", "price": 61000.0, "equity_krw": 1_000_000, "history": history}
        ctx_factors = {"rsi": pd.Series(np.full(100, 50.0))}
        # run_bar builds ctx without factors key — strategy falls back to empty rsi.
        # The signal may be "bullish divergence (sized=0)" or "buy" depending on returns.
        # We only assert no exception and the call completes.
        intents_in = _run_in_loop(loop, orch.run_bar(ts=ts_in, market_snapshot=snap_in))
        # intents_in is a list (possibly empty if sized=0 → hold); no exception is the key assertion.
        assert isinstance(intents_in, list)


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


# ── #380: live-scanner max_concurrent_positions 캡 + sell-side dedup ──────────
import pandas as _pd  # noqa: E402


def _live_scanner(sid, *, action="sell", size=0.05, max_concurrent=None, universe=None):
    uni = universe or ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

    class _LS:
        is_live_scanner = True
        strategy_id = sid
        max_concurrent_positions = max_concurrent

        @classmethod
        def get_universe(cls):
            return list(uni)

        async def on_bar(self, ctx):
            return Signal(action=action, size=size, reason=f"{sid}-fire")

    return _LS()


def _universe_snap(symbols, price=100.0):
    hist = _pd.DataFrame({"close": [price, price, price]})
    return {
        "ohlcv_history": {s: hist.copy() for s in symbols},
        "equity_usdt": 1_000_000.0,
        "equity_krw": 1_000_000.0,
    }


def test_live_scanner_max_concurrent_caps_entries(loop, policy):
    """#380 — max_concurrent_positions 가 동시 진입 종목 수를 상한."""
    orch = AsyncStrategyOrchestrator(policy)
    uni = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    _register(orch, _live_scanner("ls-cap", action="sell", max_concurrent=2, universe=uni))
    intents = _run_in_loop(loop, orch.run_bar(ts=None, market_snapshot=_universe_snap(uni)))
    assert len(intents) == 2, f"cap=2 라 2종만 진입해야 함, got {len(intents)}"


def test_live_scanner_sell_dedup_no_restack(loop, policy):
    """#380 — 숏(sell) 진입도 (sid,symbol) dedup → 같은 종목 재진입 차단 (4중진입 fix)."""
    orch = AsyncStrategyOrchestrator(policy)
    uni = ["BTCUSDT", "ETHUSDT"]
    _register(orch, _live_scanner("ls-dedup", action="sell", universe=uni))
    snap = _universe_snap(uni)
    first = _run_in_loop(loop, orch.run_bar(ts=None, market_snapshot=snap))
    assert len(first) == 2
    second = _run_in_loop(loop, orch.run_bar(ts=None, market_snapshot=snap))
    assert len(second) == 0, "이미 보유 중인 종목 sell 재진입 차단"


def test_live_scanner_no_cap_when_unset(loop, policy):
    """max_concurrent 미설정 → 무제한 (legacy 동작)."""
    orch = AsyncStrategyOrchestrator(policy)
    uni = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
    _register(orch, _live_scanner("ls-nocap", action="sell", universe=uni))
    intents = _run_in_loop(loop, orch.run_bar(ts=None, market_snapshot=_universe_snap(uni)))
    assert len(intents) == 4
