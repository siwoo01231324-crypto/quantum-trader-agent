"""Tests for strategy ON/OFF toggle (#180).

Covers:
- AsyncStrategyOrchestrator.enable_strategy / disable_strategy
- Disabled strategies skipped by run_bar (signal blocking)
- disable_strategy returns liquidation OrderIntents for held positions (D1)
- WAL strategy_toggled event emission via observer

User decisions baked into tests:
- D1: disable → immediate liquidation (sell market) for held positions
- D2: warning dialog handled in UI layer (not tested here)
"""
from __future__ import annotations

import asyncio

import pytest

from backtest.protocol import Signal
from portfolio import AsyncStrategyOrchestrator, OrderIntent
from risk.dsl import Policy
from src.live.types import WALEvent


# ---------------------------------------------------------------------------
# Helpers (parallel to test_portfolio_orchestrator_async.py)
# ---------------------------------------------------------------------------

def _make_policy(**kwargs) -> Policy:
    return Policy(policy_version=1, name="toggle-test-policy", **kwargs)


def _sync_strategy(strategy_id: str, action: str = "buy", size: float = 0.1):
    class _S:
        def on_bar(self, ctx: object) -> Signal:
            return Signal(action=action, size=size, reason=f"{strategy_id}-signal")

    s = _S()
    s.strategy_id = strategy_id
    return s


def _register(orch: AsyncStrategyOrchestrator, strat) -> None:
    orch.register_strategy(strat.strategy_id, strat)


SNAP = {"symbol": "005930", "price": 70000.0, "equity_krw": 1_000_000}


def _run(loop: asyncio.AbstractEventLoop, coro):
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
def wal_events():
    """Captured WAL events from the orchestrator's observer."""
    return []


@pytest.fixture
def orchestrator(policy, wal_events):
    """Orchestrator wired with a list-collecting WAL observer."""
    return AsyncStrategyOrchestrator(policy, wal_observer=wal_events.append)


# ---------------------------------------------------------------------------
# enable_strategy / disable_strategy state
# ---------------------------------------------------------------------------

def test_strategy_enabled_by_default(orchestrator):
    """Newly registered strategy is enabled — no need to call enable_strategy explicitly."""
    _register(orchestrator, _sync_strategy("s1"))
    assert orchestrator.is_enabled("s1") is True
    assert "s1" not in orchestrator.disabled_strategies


def test_disable_strategy_marks_disabled(orchestrator):
    _register(orchestrator, _sync_strategy("s1"))
    orchestrator.disable_strategy("s1")
    assert orchestrator.is_enabled("s1") is False
    assert "s1" in orchestrator.disabled_strategies


def test_enable_strategy_clears_disabled(orchestrator):
    _register(orchestrator, _sync_strategy("s1"))
    orchestrator.disable_strategy("s1")
    orchestrator.enable_strategy("s1")
    assert orchestrator.is_enabled("s1") is True
    assert "s1" not in orchestrator.disabled_strategies


def test_disable_strategy_idempotent(orchestrator, wal_events):
    """Disabling twice should still keep state disabled (no errors), and emit only ONE WAL audit on the actual transition."""
    _register(orchestrator, _sync_strategy("s1"))
    orchestrator.disable_strategy("s1")
    orchestrator.disable_strategy("s1")
    assert orchestrator.is_enabled("s1") is False
    toggled = [e for e in wal_events if e.event_type == "strategy_toggled"]
    assert len(toggled) == 1, "second disable on already-disabled strategy must not emit duplicate audit"


def test_enable_strategy_idempotent(orchestrator, wal_events):
    _register(orchestrator, _sync_strategy("s1"))
    orchestrator.enable_strategy("s1")  # no-op (already enabled)
    orchestrator.enable_strategy("s1")
    toggled = [e for e in wal_events if e.event_type == "strategy_toggled"]
    assert len(toggled) == 0, "enable on already-enabled strategy must not emit audit"


def test_disable_strategy_unknown_raises(orchestrator):
    """Disabling an unregistered strategy is a programming error → ValueError."""
    with pytest.raises(ValueError, match="not registered"):
        orchestrator.disable_strategy("ghost-strategy")


# ---------------------------------------------------------------------------
# run_bar gating
# ---------------------------------------------------------------------------

def test_disabled_strategy_skipped_by_run_bar(orchestrator, loop):
    """Disabled strategy must not contribute OrderIntents in run_bar."""
    _register(orchestrator, _sync_strategy("s-on"))
    _register(orchestrator, _sync_strategy("s-off"))
    orchestrator.disable_strategy("s-off")

    intents = _run(loop, orchestrator.run_bar(ts=None, market_snapshot=SNAP))
    ids = {i.strategy_id for i in intents}
    assert "s-on" in ids
    assert "s-off" not in ids


def test_re_enabled_strategy_resumes_signals(orchestrator, loop):
    """After disable → enable, strategy resumes producing OrderIntents."""
    _register(orchestrator, _sync_strategy("s1"))
    orchestrator.disable_strategy("s1")
    orchestrator.enable_strategy("s1")

    intents = _run(loop, orchestrator.run_bar(ts=None, market_snapshot=SNAP))
    assert any(i.strategy_id == "s1" for i in intents)


def test_run_bar_strategies_filter_skips_disabled(orchestrator, loop):
    """Even if `strategies=` filter explicitly names a disabled one, it stays blocked."""
    _register(orchestrator, _sync_strategy("s1"))
    _register(orchestrator, _sync_strategy("s2"))
    orchestrator.disable_strategy("s1")

    intents = _run(loop, orchestrator.run_bar(ts=None, market_snapshot=SNAP, strategies=["s1", "s2"]))
    ids = {i.strategy_id for i in intents}
    assert "s1" not in ids
    assert "s2" in ids


# ---------------------------------------------------------------------------
# Liquidation on disable (D1: 즉시 청산)
# ---------------------------------------------------------------------------

def test_disable_with_positions_returns_sell_intents(orchestrator):
    """D1: disable_strategy with held positions returns market-sell OrderIntents.

    Caller (REST handler) is responsible for actually submitting these to the broker.
    """
    _register(orchestrator, _sync_strategy("s1"))
    held = [("005930", 50.0), ("000660", 20.0)]
    intents = orchestrator.disable_strategy("s1", positions=held)

    assert len(intents) == 2
    for intent in intents:
        assert isinstance(intent, OrderIntent)
        assert intent.strategy_id == "s1"
        assert intent.side == "sell"
        assert "liquidation" in intent.reason.lower() or "disable" in intent.reason.lower()
    symbols = {i.symbol for i in intents}
    assert symbols == {"005930", "000660"}
    qtys = {i.symbol: i.qty for i in intents}
    assert qtys["005930"] == 50.0
    assert qtys["000660"] == 20.0


def test_disable_without_positions_returns_empty_list(orchestrator):
    _register(orchestrator, _sync_strategy("s1"))
    intents = orchestrator.disable_strategy("s1")
    assert intents == []


def test_disable_with_zero_qty_position_skipped(orchestrator):
    """Zero-qty position → no liquidation intent (avoid empty broker calls)."""
    _register(orchestrator, _sync_strategy("s1"))
    held = [("005930", 0.0), ("000660", 10.0)]
    intents = orchestrator.disable_strategy("s1", positions=held)
    assert len(intents) == 1
    assert intents[0].symbol == "000660"


# ---------------------------------------------------------------------------
# WAL audit (strategy_toggled event)
# ---------------------------------------------------------------------------

def test_disable_emits_wal_strategy_toggled(orchestrator, wal_events):
    _register(orchestrator, _sync_strategy("s1"))
    orchestrator.disable_strategy("s1")
    assert len(wal_events) == 1
    ev = wal_events[0]
    assert isinstance(ev, WALEvent)
    assert ev.event_type == "strategy_toggled"
    assert ev.payload["strategy_id"] == "s1"
    assert ev.payload["enabled"] is False
    assert ev.ts  # UTC ISO 8601 string, non-empty


def test_enable_emits_wal_strategy_toggled(orchestrator, wal_events):
    _register(orchestrator, _sync_strategy("s1"))
    orchestrator.disable_strategy("s1")
    wal_events.clear()
    orchestrator.enable_strategy("s1")
    assert len(wal_events) == 1
    ev = wal_events[0]
    assert ev.event_type == "strategy_toggled"
    assert ev.payload["strategy_id"] == "s1"
    assert ev.payload["enabled"] is True


def test_wal_observer_optional(policy, loop):
    """Orchestrator created without observer should still toggle without error."""
    orch = AsyncStrategyOrchestrator(policy)  # no wal_observer
    _register(orch, _sync_strategy("s1"))
    orch.disable_strategy("s1")  # must not raise
    assert orch.is_enabled("s1") is False


def test_wal_observer_exception_swallowed(policy):
    """If WAL observer raises, toggle state must still succeed (audit is best-effort)."""
    def raising_observer(ev):
        raise RuntimeError("disk full")

    orch = AsyncStrategyOrchestrator(policy, wal_observer=raising_observer)
    _register(orch, _sync_strategy("s1"))
    orch.disable_strategy("s1")  # must not raise
    assert orch.is_enabled("s1") is False
