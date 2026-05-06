"""Integration: StrategyPositionStore + /api/strategies/{id}/toggle (#192).

Validates the end-to-end wiring: fills accumulate in StrategyPositionStore,
which is exposed through DashboardState.position_provider, and toggling a
strategy OFF emits liquidation OrderIntents that match exactly the holdings
attributed to that strategy (and nothing else).

This complements the unit suite in tests/live/test_strategy_position_store.py
and the lambda-based toggle suite in tests/test_dashboard_strategies.py.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard.app import DashboardState, create_app
from src.live.strategy_position_store import StrategyPositionStore
from src.live.types import WALEvent
from src.live.wal import WAL
from portfolio import AsyncStrategyOrchestrator
from risk.dsl import Policy


class _StubStrategy:
    def __init__(self, sid: str):
        self.strategy_id = sid

    def on_bar(self, ctx):
        return None


@pytest.fixture
def specs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "docs" / "specs" / "strategies"
    d.mkdir(parents=True)
    (d / "alpha.md").write_text(
        """---
type: strategy
id: alpha
name: Alpha
status: paper
instruments: [BTCUSDT]
timeframe: 4h
owner: tester
created: 2026-01-01
---
""",
        encoding="utf-8",
    )
    (d / "beta.md").write_text(
        """---
type: strategy
id: beta
name: Beta
status: paper
instruments: [005930]
timeframe: 1d
owner: tester
created: 2026-01-01
---
""",
        encoding="utf-8",
    )
    return d


@pytest.fixture
def store() -> StrategyPositionStore:
    return StrategyPositionStore()


@pytest.fixture
def state(specs_dir: Path, store: StrategyPositionStore) -> DashboardState:
    s = DashboardState()
    s.specs_dir = specs_dir
    orch = AsyncStrategyOrchestrator(Policy(policy_version=1, name="t"))
    orch.register_strategy("alpha", _StubStrategy("alpha"))
    orch.register_strategy("beta", _StubStrategy("beta"))
    s.orchestrator = orch
    s.position_provider = store.get_positions
    return s


@pytest.fixture
def client(state: DashboardState) -> TestClient:
    return TestClient(create_app(state))


def test_fill_then_toggle_off_liquidates_only_that_strategy(
    client: TestClient, store: StrategyPositionStore,
):
    """alpha buys 0.5 BTC, beta buys 100 005930 → toggle alpha OFF →
    only the BTCUSDT position liquidates."""
    store.record_fill(strategy_id="alpha", symbol="BTCUSDT", side="buy", qty=Decimal("0.5"))
    store.record_fill(strategy_id="beta", symbol="005930", side="buy", qty=Decimal("100"))

    resp = client.post("/api/strategies/alpha/toggle", json={"enabled": False})
    body = resp.json()

    intents = body["liquidation_intents"]
    assert len(intents) == 1
    assert intents[0]["strategy_id"] == "alpha"
    assert intents[0]["symbol"] == "BTCUSDT"
    assert intents[0]["qty"] == 0.5
    assert intents[0]["side"] == "sell"


def test_other_strategies_positions_not_touched(
    client: TestClient, store: StrategyPositionStore,
):
    store.record_fill(strategy_id="alpha", symbol="BTCUSDT", side="buy", qty=Decimal("0.5"))
    store.record_fill(strategy_id="beta", symbol="005930", side="buy", qty=Decimal("100"))

    client.post("/api/strategies/alpha/toggle", json={"enabled": False})
    # beta still holds its 100 shares
    assert store.get_positions("beta") == [("005930", 100.0)]


def test_toggle_off_with_empty_store_returns_empty_intents(client: TestClient):
    """No fills recorded for alpha → liquidation list empty (graceful)."""
    resp = client.post("/api/strategies/alpha/toggle", json={"enabled": False})
    body = resp.json()
    assert body["liquidation_intents"] == []


def test_wal_replay_then_toggle_off_works(
    tmp_path: Path, specs_dir: Path,
):
    """Boot scenario: store reconstructs positions from WAL, then toggle OFF
    emits liquidation intents using the replayed state."""
    wal_path = tmp_path / "wal.jsonl"
    wal = WAL(wal_path)
    wal.write(WALEvent(
        ts="2026-05-06T00:00:00+00:00",
        event_type="order_filled",
        payload={
            "client_order_id": "alpha:BTCUSDT:1700000000000:0",
            "strategy_id": "alpha",
            "symbol": "BTCUSDT",
            "side": "buy",
            "fill_qty": "0.75",
        },
    ))

    store = StrategyPositionStore()
    store.replay_from_wal(wal_path)

    s = DashboardState()
    s.specs_dir = specs_dir
    orch = AsyncStrategyOrchestrator(Policy(policy_version=1, name="t"))
    orch.register_strategy("alpha", _StubStrategy("alpha"))
    orch.register_strategy("beta", _StubStrategy("beta"))
    s.orchestrator = orch
    s.position_provider = store.get_positions

    c = TestClient(create_app(s))
    resp = c.post("/api/strategies/alpha/toggle", json={"enabled": False})
    intents = resp.json()["liquidation_intents"]

    assert len(intents) == 1
    assert intents[0]["symbol"] == "BTCUSDT"
    assert intents[0]["qty"] == 0.75
