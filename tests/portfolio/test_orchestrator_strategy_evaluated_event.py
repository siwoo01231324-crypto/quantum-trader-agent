"""S5 (#231) — `strategy_evaluated` WAL event emit on every run_bar dispatch.

Each `(strategy_id, symbol)` pair in a tick produces exactly one event
regardless of buy/sell/hold/exception outcome — used by AC0_strategy_dispatch
(11 strategies × ≥1 event) and AC5 (24h ≥ 1000 events).

Decision values:
    - "buy" / "sell" — strategy emitted Signal(action=...)
    - "hold" reason="no_signal" — strategy returned None
    - "hold" reason="action_hold" — strategy returned Signal(action="hold")
    - "exception" — strategy.on_bar raised
"""
from __future__ import annotations

import asyncio
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from portfolio import AsyncStrategyOrchestrator
from risk.dsl import Policy


# ---------- Fakes -----------------------------------------------------------

class _AlwaysBuy:
    is_live_scanner: ClassVar[bool] = False

    async def on_bar(self, ctx) -> Signal | None:
        return Signal(action="buy", size=0.05, reason="test_buy")


class _AlwaysNone:
    is_live_scanner: ClassVar[bool] = False

    async def on_bar(self, ctx) -> Signal | None:
        return None


class _AlwaysHold:
    is_live_scanner: ClassVar[bool] = False

    async def on_bar(self, ctx) -> Signal | None:
        return Signal(action="hold", size=0.0, reason="test_hold")


class _AlwaysThrow:
    is_live_scanner: ClassVar[bool] = False

    async def on_bar(self, ctx) -> Signal | None:
        raise RuntimeError("test_exc")


# ---------- Fixtures --------------------------------------------------------

@pytest.fixture
def policy() -> Policy:
    return Policy(policy_version=1, name="s5-test")


@pytest.fixture
def snap_005930() -> dict:
    return {"symbol": "005930", "price": 70000.0, "equity_krw": 10_000_000.0}


def _events_for(observer_log: list, strategy_id: str) -> list:
    return [e for e in observer_log if e.payload.get("strategy_id") == strategy_id
            and e.event_type == "strategy_evaluated"]


# ---------- Tests -----------------------------------------------------------

@pytest.mark.asyncio
async def test_buy_signal_emits_strategy_evaluated(policy, snap_005930):
    log: list = []
    orch = AsyncStrategyOrchestrator(policy=policy, wal_observer=log.append)
    orch.register_strategy("buy_strat", _AlwaysBuy())

    await orch.run_bar(ts=pd.Timestamp("2026-05-14T09:00:00+00:00"),
                       market_snapshot=snap_005930)

    events = _events_for(log, "buy_strat")
    assert len(events) == 1
    ev = events[0]
    assert ev.payload["decision"] == "buy"
    assert ev.payload["symbol"] == "005930"
    assert ev.payload["reason"] == "test_buy"
    assert ev.event_type == "strategy_evaluated"


@pytest.mark.asyncio
async def test_none_signal_emits_hold_no_signal(policy, snap_005930):
    log: list = []
    orch = AsyncStrategyOrchestrator(policy=policy, wal_observer=log.append)
    orch.register_strategy("none_strat", _AlwaysNone())

    await orch.run_bar(ts=pd.Timestamp("2026-05-14T09:00:00+00:00"),
                       market_snapshot=snap_005930)

    events = _events_for(log, "none_strat")
    assert len(events) == 1
    assert events[0].payload["decision"] == "hold"
    assert events[0].payload["reason"] == "no_signal"


@pytest.mark.asyncio
async def test_hold_signal_emits_hold_action_hold(policy, snap_005930):
    log: list = []
    orch = AsyncStrategyOrchestrator(policy=policy, wal_observer=log.append)
    orch.register_strategy("hold_strat", _AlwaysHold())

    await orch.run_bar(ts=pd.Timestamp("2026-05-14T09:00:00+00:00"),
                       market_snapshot=snap_005930)

    events = _events_for(log, "hold_strat")
    assert len(events) == 1
    assert events[0].payload["decision"] == "hold"
    assert events[0].payload["reason"] == "action_hold"


@pytest.mark.asyncio
async def test_exception_emits_strategy_evaluated_with_exception_type(policy, snap_005930):
    log: list = []
    orch = AsyncStrategyOrchestrator(policy=policy, wal_observer=log.append)
    orch.register_strategy("throw_strat", _AlwaysThrow())

    await orch.run_bar(ts=pd.Timestamp("2026-05-14T09:00:00+00:00"),
                       market_snapshot=snap_005930)

    events = _events_for(log, "throw_strat")
    assert len(events) == 1
    assert events[0].payload["decision"] == "exception"
    assert events[0].payload["reason"] == "RuntimeError"


@pytest.mark.asyncio
async def test_no_observer_no_throw(policy, snap_005930):
    """orchestrator must not crash when wal_observer=None (default case)."""
    orch = AsyncStrategyOrchestrator(policy=policy, wal_observer=None)
    orch.register_strategy("buy_strat", _AlwaysBuy())
    # Should not raise
    await orch.run_bar(ts=pd.Timestamp("2026-05-14T09:00:00+00:00"),
                       market_snapshot=snap_005930)


@pytest.mark.asyncio
async def test_observer_exception_swallowed(policy, snap_005930):
    """wal_observer exception must not break run_bar (logged warning only)."""
    def broken_observer(ev):
        raise IOError("disk full")
    orch = AsyncStrategyOrchestrator(policy=policy, wal_observer=broken_observer)
    orch.register_strategy("buy_strat", _AlwaysBuy())
    # Should not raise
    intents = await orch.run_bar(
        ts=pd.Timestamp("2026-05-14T09:00:00+00:00"),
        market_snapshot=snap_005930,
    )
    # buy intent still produced — order routing unaffected by observer failure
    assert len(intents) >= 1


# ---------- Live-scanner per-symbol case ------------------------------------

class _AlwaysBuyLiveScanner(LiveScannerMixin):
    async def on_bar(self, ctx) -> Signal | None:
        return Signal(action="buy", size=0.05,
                      reason=f"buy:{ctx['market_snapshot']['symbol']}")


def _ohlcv(symbol: str, n: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(abs(hash(symbol)) % (2**32))
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    close = np.maximum(close, 1.0)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": close, "high": close * 1.001, "low": close * 0.999,
         "close": close, "volume": np.full(n, 1000.0)},
        index=idx,
    )


@pytest.mark.asyncio
async def test_live_scanner_emits_one_event_per_symbol(policy):
    """live-scanner per-symbol dispatch: N symbols → N events per strategy."""
    log: list = []
    orch = AsyncStrategyOrchestrator(policy=policy, wal_observer=log.append)
    orch.register_strategy("ls_strat", _AlwaysBuyLiveScanner())

    universe = ["005930", "035720", "000660"]
    market_snapshot = {
        "ohlcv_history": {s: _ohlcv(s) for s in universe},
        "equity_krw": 10_000_000.0,
    }
    await orch.run_bar(ts=pd.Timestamp("2026-05-14T09:00:00+00:00"),
                       market_snapshot=market_snapshot)

    events = _events_for(log, "ls_strat")
    assert len(events) == 3, f"expected 3 events (one per symbol), got {len(events)}"
    symbols_emitted = {e.payload["symbol"] for e in events}
    assert symbols_emitted == set(universe)
    for ev in events:
        assert ev.payload["decision"] == "buy"
