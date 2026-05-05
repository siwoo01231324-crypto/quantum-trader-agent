"""RunController + /api/run/* endpoints 단위 테스트 (#182 단계 2)."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.dashboard.app import DashboardState, create_app
from src.dashboard.run_controller import (
    STATUS_ERROR,
    STATUS_RUNNING,
    STATUS_STOPPED,
    RunController,
)


# ---------------------------------------------------------------------------
# RunController 단위 테스트
# ---------------------------------------------------------------------------

class TestRunControllerLifecycle:
    @pytest.mark.asyncio
    async def test_initial_state_is_stopped(self) -> None:
        rc = RunController(pipeline_factory=lambda p: asyncio.sleep(0))
        assert rc.status()["status"] == STATUS_STOPPED

    @pytest.mark.asyncio
    async def test_start_then_stop(self) -> None:
        async def _long_pipeline(_p):
            await asyncio.sleep(60)  # 충분히 김

        rc = RunController(pipeline_factory=_long_pipeline)
        result = await rc.start({"symbols": ["005930"]})
        assert result["ok"] is True
        assert rc.status()["status"] == STATUS_RUNNING
        assert rc.status()["request_params"]["symbols"] == ["005930"]

        result = await rc.stop()
        assert result["ok"] is True
        assert rc.status()["status"] == STATUS_STOPPED

    @pytest.mark.asyncio
    async def test_double_start_returns_already_running(self) -> None:
        async def _long(_p):
            await asyncio.sleep(60)

        rc = RunController(pipeline_factory=_long)
        await rc.start({})
        result = await rc.start({})
        assert result["ok"] is False
        assert "running" in result["reason"]
        await rc.stop()

    @pytest.mark.asyncio
    async def test_stop_when_not_running_returns_not_running(self) -> None:
        rc = RunController(pipeline_factory=lambda p: asyncio.sleep(0))
        result = await rc.stop()
        assert result["ok"] is False
        assert "not running" in result["reason"]

    @pytest.mark.asyncio
    async def test_factory_raise_sets_error_status(self) -> None:
        # factory 자체가 예외 (예: KIS env 누락 SystemExit) — _wrap 안에서 catch
        async def _boom(_p):
            raise RuntimeError("boom")

        rc = RunController(pipeline_factory=_boom)
        await rc.start({})
        # _wrap 이 비동기 task 안에서 실행되므로 catch 까지 잠시 대기
        for _ in range(20):
            if rc.status()["status"] == STATUS_ERROR:
                break
            await asyncio.sleep(0.05)
        assert rc.status()["status"] == STATUS_ERROR
        assert "boom" in (rc.status().get("last_error") or "")


# ---------------------------------------------------------------------------
# /api/run/* endpoints — TestClient
# ---------------------------------------------------------------------------

@pytest.fixture()
def client_with_controller() -> tuple[TestClient, RunController]:
    state = DashboardState()
    rc = RunController(pipeline_factory=lambda p: asyncio.sleep(60))
    state.run_controller = rc
    return TestClient(create_app(state)), rc


@pytest.fixture()
def client_no_controller() -> TestClient:
    state = DashboardState()
    state.run_controller = None
    return TestClient(create_app(state))


class TestRunEndpoints:
    def test_status_returns_unavailable_when_no_controller(
        self, client_no_controller: TestClient,
    ) -> None:
        resp = client_no_controller.get("/api/run/status")
        assert resp.status_code == 200
        assert resp.json() == {"available": False}

    def test_status_returns_state_when_controller(
        self, client_with_controller: tuple[TestClient, RunController],
    ) -> None:
        client, _ = client_with_controller
        resp = client.get("/api/run/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert data["status"] == STATUS_STOPPED

    def test_start_returns_503_when_no_controller(
        self, client_no_controller: TestClient,
    ) -> None:
        resp = client_no_controller.post("/api/run/start", json={})
        assert resp.status_code == 503

    def test_start_then_stop_round_trip(
        self, client_with_controller: tuple[TestClient, RunController],
    ) -> None:
        client, _ = client_with_controller
        resp = client.post("/api/run/start", json={"symbols": ["005930"]})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        resp = client.get("/api/run/status")
        assert resp.json()["status"] in (STATUS_RUNNING, STATUS_STOPPED)

        resp = client.post("/api/run/stop")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_start_twice_returns_422(
        self, client_with_controller: tuple[TestClient, RunController],
    ) -> None:
        client, _ = client_with_controller
        client.post("/api/run/start", json={})
        resp = client.post("/api/run/start", json={})
        assert resp.status_code == 422
        client.post("/api/run/stop")  # cleanup


# ---------------------------------------------------------------------------
# HTML 컨트롤 카드 — root 페이지에 버튼 + 카드 존재
# ---------------------------------------------------------------------------

class TestDashboardControlCard:
    def test_root_contains_run_control_card(self) -> None:
        client = TestClient(create_app(DashboardState()))
        body = client.get("/").text
        assert "거래 시작" in body
        assert "거래 정지" in body
        assert "run-status" in body
        assert "/api/run/start" in body or "runStart" in body
