"""Integration: PnLAggregator + DashboardState + /api/pnl + per-card pnl_today (#194)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from src.dashboard.app import DashboardState, create_app
from src.live.pnl_aggregator import PnLAggregator
from portfolio import AsyncStrategyOrchestrator
from risk.dsl import Policy


KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 5, 6, 14, 0, tzinfo=KST)


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
def aggregator() -> PnLAggregator:
    return PnLAggregator(kst_now=lambda: NOW)


@pytest.fixture
def state(specs_dir: Path, aggregator: PnLAggregator) -> DashboardState:
    s = DashboardState()
    s.specs_dir = specs_dir
    orch = AsyncStrategyOrchestrator(Policy(policy_version=1, name="t"))
    orch.register_strategy("alpha", _StubStrategy("alpha"))
    orch.register_strategy("beta", _StubStrategy("beta"))
    s.orchestrator = orch
    s.pnl_aggregator = aggregator
    return s


@pytest.fixture
def client(state: DashboardState) -> TestClient:
    return TestClient(create_app(state))


def test_api_pnl_reflects_aggregator(
    client: TestClient, aggregator: PnLAggregator,
):
    """alpha realises +10, beta loses -5 → /api/pnl realtime = 5."""
    aggregator.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"), ts=NOW,
    )
    aggregator.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("110"), fee=Decimal("0"), ts=NOW,
    )
    aggregator.record_fill(
        strategy_id="beta", symbol="005930", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("5"), ts=NOW,
    )

    body = client.get("/api/pnl").json()
    assert body["realtime"] == 5.0
    assert body["daily"] == 5.0
    assert body["monthly"] == 5.0
    assert body["by_strategy"] == {"alpha": 10.0, "beta": -5.0}


def test_per_strategy_card_shows_pnl_today(
    client: TestClient, aggregator: PnLAggregator,
):
    """Each strategy card carries its own pnl_today."""
    aggregator.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"), ts=NOW,
    )
    aggregator.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("130"), fee=Decimal("0"), ts=NOW,
    )

    items = client.get("/api/strategies").json()
    alpha = next(it for it in items if it["id"] == "alpha")
    beta = next(it for it in items if it["id"] == "beta")
    assert alpha["pnl_today"] == 30.0
    assert beta["pnl_today"] == 0.0


def test_empty_aggregator_returns_zeros(client: TestClient):
    body = client.get("/api/pnl").json()
    assert body["realtime"] == 0.0
    assert body["daily"] == 0.0
    assert body["monthly"] == 0.0
    assert body["by_strategy"] == {}


def test_no_aggregator_falls_back_to_state_defaults(specs_dir: Path):
    """Without `pnl_aggregator` injected, /api/pnl reads state.pnl_* (defaults
    or whatever was set externally). Backwards-compat path."""
    s = DashboardState()
    s.specs_dir = specs_dir
    s.pnl_realtime = 1234.5
    s.pnl_daily = 50.0
    s.pnl_monthly = 200.0
    c = TestClient(create_app(s))

    body = c.get("/api/pnl").json()
    assert body["realtime"] == 1234.5
    assert body["daily"] == 50.0
    assert body["monthly"] == 200.0
    assert body["by_strategy"] == {}


def test_api_strategies_pnl_today_zero_when_aggregator_missing(
    specs_dir: Path,
):
    """No aggregator → cards still render, pnl_today = 0.0."""
    s = DashboardState()
    s.specs_dir = specs_dir
    c = TestClient(create_app(s))

    items = c.get("/api/strategies").json()
    for it in items:
        assert it["pnl_today"] == 0.0
