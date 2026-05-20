"""Tests for /strategies catalog page + /api/strategies REST + toggle (#178 + #180).

Covers:
- GET /api/strategies — JSON catalog with enabled state merged from orchestrator
- GET /strategies — HTML card grid
- POST /api/strategies/{id}/toggle — calls enable/disable and returns liquidation
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard.app import DashboardState, create_app
from portfolio import AsyncStrategyOrchestrator
from risk.dsl import Policy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def specs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "docs" / "specs" / "strategies"
    d.mkdir(parents=True)
    (d / "alpha.md").write_text(
        """---
type: strategy
id: alpha
name: Alpha Test
status: live
instruments: [005930]
timeframe: 15m
owner: tester
created: 2026-01-01
sharpe_bt: 1.5
mdd_bt: -0.12
annual_return_bt: 0.18
---
""", encoding="utf-8")
    (d / "beta.md").write_text(
        """---
type: strategy
id: beta
name: Beta Test
status: paper
instruments: [BTCUSDT]
timeframe: 4h
owner: tester
created: 2026-02-01
sharpe_bt: 0.8
---
""", encoding="utf-8")
    return d


class _StubStrategy:
    def __init__(self, sid: str):
        self.strategy_id = sid

    def on_bar(self, ctx):
        return None


@pytest.fixture
def state(specs_dir: Path) -> DashboardState:
    s = DashboardState()
    s.specs_dir = specs_dir
    orch = AsyncStrategyOrchestrator(Policy(policy_version=1, name="t"))
    orch.register_strategy("alpha", _StubStrategy("alpha"))
    orch.register_strategy("beta", _StubStrategy("beta"))
    s.orchestrator = orch
    return s


@pytest.fixture
def client(state: DashboardState) -> TestClient:
    return TestClient(create_app(state))


# ---------------------------------------------------------------------------
# GET /api/strategies (#178)
# ---------------------------------------------------------------------------

class TestStrategiesJSON:
    def test_returns_200(self, client: TestClient):
        assert client.get("/api/strategies").status_code == 200

    def test_returns_list_of_catalog_entries(self, client: TestClient):
        items = client.get("/api/strategies").json()
        assert isinstance(items, list)
        assert len(items) == 2

    def test_catalog_entry_shape(self, client: TestClient):
        items = client.get("/api/strategies").json()
        alpha = next(it for it in items if it["id"] == "alpha")
        assert alpha["name"] == "Alpha Test"
        assert alpha["status"] == "live"
        assert alpha["instruments"] == ["005930"]
        assert alpha["timeframe"] == "15m"
        assert alpha["sharpe_bt"] == 1.5
        assert alpha["mdd_bt"] == -0.12
        assert alpha["enabled"] is True  # default ON

    def test_disabled_strategy_reflected_in_json(self, client: TestClient, state: DashboardState):
        state.orchestrator.disable_strategy("alpha")
        items = client.get("/api/strategies").json()
        alpha = next(it for it in items if it["id"] == "alpha")
        assert alpha["enabled"] is False

    def test_unregistered_strategy_not_in_yaml_shows_off(
        self, client: TestClient, state: DashboardState,
    ):
        """2026-05-20 truthful derivation — spec 가 orch 미등록이고 production.yaml
        에도 없으면 (absent) UI 에서 OFF 로 보여야 한다 (예전엔 default True 였음)."""
        from portfolio import AsyncStrategyOrchestrator
        from risk.dsl import Policy
        state.orchestrator = AsyncStrategyOrchestrator(Policy(policy_version=1, name="t2"))
        state.orchestrator.register_strategy("alpha", _StubStrategy("alpha"))
        # no production_yaml_path on the fixture → load_production_status
        # returns {} → 'beta' resolves to "absent".
        items = client.get("/api/strategies").json()
        beta = next(it for it in items if it["id"] == "beta")
        assert beta["enabled"] is False
        assert beta["toggle_disabled"] is True
        assert beta["disabled_reason"] == "absent"


# ---------------------------------------------------------------------------
# GET /strategies (HTML — #178)
# ---------------------------------------------------------------------------

class TestStrategiesHTML:
    def test_returns_200_html(self, client: TestClient):
        resp = client.get("/strategies")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_renders_card_for_each_strategy(self, client: TestClient):
        body = client.get("/strategies").text
        assert "Alpha Test" in body
        assert "Beta Test" in body
        assert "alpha" in body and "beta" in body

    def test_renders_metrics(self, client: TestClient):
        body = client.get("/strategies").text
        # Sharpe + MDD displayed for alpha
        assert "1.5" in body
        assert "-0.12" in body or "-12" in body  # tolerate %-formatted display

    def test_renders_toggle_switch_per_card(self, client: TestClient):
        body = client.get("/strategies").text
        # Toggle for each strategy
        assert body.count('data-strategy-id="alpha"') >= 1
        assert body.count('data-strategy-id="beta"') >= 1

    def test_renders_disabled_state_visually(self, client: TestClient, state: DashboardState):
        state.orchestrator.disable_strategy("alpha")
        body = client.get("/strategies").text
        # The disabled strategy card should have a marker class or attribute
        assert "disabled" in body.lower() or "off" in body.lower()


# ---------------------------------------------------------------------------
# POST /api/strategies/{id}/toggle (#180)
# ---------------------------------------------------------------------------

class TestStrategyToggleEndpoint:
    def test_disable_returns_200(self, client: TestClient):
        resp = client.post("/api/strategies/alpha/toggle", json={"enabled": False})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["strategy_id"] == "alpha"
        assert body["enabled"] is False

    def test_disable_marks_orchestrator(self, client: TestClient, state: DashboardState):
        client.post("/api/strategies/alpha/toggle", json={"enabled": False})
        assert state.orchestrator.is_enabled("alpha") is False

    def test_enable_marks_orchestrator(self, client: TestClient, state: DashboardState):
        state.orchestrator.disable_strategy("alpha")
        client.post("/api/strategies/alpha/toggle", json={"enabled": True})
        assert state.orchestrator.is_enabled("alpha") is True

    def test_disable_with_position_provider_returns_liquidation_intents(
        self, client: TestClient, state: DashboardState,
    ):
        state.position_provider = lambda sid: [("005930", 50.0)] if sid == "alpha" else []
        resp = client.post("/api/strategies/alpha/toggle", json={"enabled": False})
        body = resp.json()
        assert "liquidation_intents" in body
        intents = body["liquidation_intents"]
        assert len(intents) == 1
        assert intents[0]["symbol"] == "005930"
        assert intents[0]["qty"] == 50.0
        assert intents[0]["side"] == "sell"

    def test_disable_without_position_provider_empty_intents(self, client: TestClient):
        resp = client.post("/api/strategies/alpha/toggle", json={"enabled": False})
        body = resp.json()
        assert body["liquidation_intents"] == []

    def test_unknown_strategy_returns_404(self, client: TestClient):
        resp = client.post("/api/strategies/ghost/toggle", json={"enabled": False})
        assert resp.status_code == 404

    def test_orchestrator_missing_returns_503(self, specs_dir: Path):
        """If state has no orchestrator wired, toggle endpoint returns 503."""
        s = DashboardState()
        s.specs_dir = specs_dir
        # no orchestrator
        c = TestClient(create_app(s))
        resp = c.post("/api/strategies/alpha/toggle", json={"enabled": False})
        assert resp.status_code == 503

    def test_invalid_body_returns_422(self, client: TestClient):
        resp = client.post("/api/strategies/alpha/toggle", json={})
        # missing required `enabled` field → FastAPI 422 OR our handler 400
        assert resp.status_code in (400, 422)
