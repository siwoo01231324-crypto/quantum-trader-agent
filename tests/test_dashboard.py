"""Integration tests for FastAPI dashboard (src/dashboard/app.py).

Uses FastAPI TestClient to inject mock data and verify UI rendering.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.dashboard.app import create_app, DashboardState


@pytest.fixture()
def state() -> DashboardState:
    s = DashboardState()
    # 손익 데이터
    s.pnl_realtime = 123456.78
    s.pnl_daily = 45000.0
    s.pnl_monthly = 180000.0
    # 한도 사용률 6종 (0.0~1.0)
    s.limit_per_trade = 0.45
    s.limit_per_day = 0.62
    s.limit_per_portfolio = 0.30
    s.limit_per_position = 0.55
    s.limit_sector = 0.20
    s.limit_drawdown = 0.08
    # 타임라인
    s.timeline_events = [
        {"ts": "2026-04-27T09:00:00", "type": "signal", "detail": "BUY BTC"},
        {"ts": "2026-04-27T09:00:01", "type": "metalabel", "detail": "PASS 0.82"},
        {"ts": "2026-04-27T09:00:02", "type": "order", "detail": "LIMIT 1.0 BTC"},
        {"ts": "2026-04-27T09:00:03", "type": "fill", "detail": "FILLED @ 88000"},
    ]
    # 킬스위치 상태
    s.kill_switch_triggers = {
        "drawdown": False,
        "daily_loss": False,
        "manual": False,
        "risk_breach": False,
    }
    s.kill_switch_last_triggered = None
    return s


@pytest.fixture()
def client(state: DashboardState) -> TestClient:
    app = create_app(state)
    return TestClient(app)


class TestMetricsEndpoint:
    def test_metrics_returns_200(self, client: TestClient) -> None:
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_metrics_content_type_prometheus(self, client: TestClient) -> None:
        resp = client.get("/metrics")
        assert "text/plain" in resp.headers["content-type"]

    def test_metrics_contains_qta_prefix(self, client: TestClient) -> None:
        resp = client.get("/metrics")
        assert "qta_" in resp.text or "# HELP" in resp.text or "# TYPE" in resp.text


class TestDashboardRoot:
    def test_root_returns_200(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200

    def test_root_is_html(self, client: TestClient) -> None:
        resp = client.get("/")
        assert "text/html" in resp.headers["content-type"]

    def test_root_has_four_quadrants(self, client: TestClient) -> None:
        html = resp = client.get("/")
        body = resp.text
        # 4사분면 섹션 식별자
        assert "pnl" in body.lower() or "손익" in body
        assert "limit" in body.lower() or "한도" in body
        assert "timeline" in body.lower() or "타임라인" in body
        assert "kill" in body.lower() or "비상정지" in body

    def test_root_shows_pnl_values(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "123456" in body or "123,456" in body

    def test_root_shows_limit_gauges(self, client: TestClient) -> None:
        body = client.get("/").text
        # 6종 한도 이름 모두 존재
        for limit in ("per_trade", "per_day", "per_portfolio", "per_position", "sector", "drawdown"):
            assert limit in body

    def test_root_shows_timeline_events(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "BUY BTC" in body
        assert "FILLED" in body

    def test_root_shows_kill_switch_triggers(self, client: TestClient) -> None:
        body = client.get("/").text
        for trigger in ("drawdown", "daily_loss", "manual", "risk_breach"):
            assert trigger in body

    def test_root_has_kill_switch_buttons(self, client: TestClient) -> None:
        body = client.get("/").text
        assert "발동" in body or "trigger" in body.lower()
        assert "해제" in body or "reset" in body.lower()


class TestKillSwitchAPI:
    def test_trigger_kill_switch(self, client: TestClient, state: DashboardState) -> None:
        resp = client.post("/api/kill-switch/trigger", json={"reason": "manual"})
        assert resp.status_code == 200
        assert state.kill_switch_triggers["manual"] is True
        assert state.kill_switch_last_triggered is not None

    def test_reset_kill_switch(self, client: TestClient, state: DashboardState) -> None:
        state.kill_switch_triggers["manual"] = True
        resp = client.post("/api/kill-switch/reset", json={"reason": "manual"})
        assert resp.status_code == 200
        assert state.kill_switch_triggers["manual"] is False

    def test_kill_switch_state_endpoint(self, client: TestClient) -> None:
        resp = client.get("/api/kill-switch")
        assert resp.status_code == 200
        data = resp.json()
        assert "triggers" in data
        assert "last_triggered" in data


class TestPnLAPI:
    def test_pnl_endpoint(self, client: TestClient) -> None:
        resp = client.get("/api/pnl")
        assert resp.status_code == 200
        data = resp.json()
        assert "realtime" in data
        assert "daily" in data
        assert "monthly" in data
        assert data["realtime"] == pytest.approx(123456.78)


class TestLimitsAPI:
    def test_limits_endpoint(self, client: TestClient) -> None:
        resp = client.get("/api/limits")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("per_trade", "per_day", "per_portfolio", "per_position", "sector", "drawdown"):
            assert key in data
        assert data["per_trade"] == pytest.approx(0.45)
