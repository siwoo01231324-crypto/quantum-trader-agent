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


class TestRunModeResolution:
    """대시보드 두 버튼(에어본/스윙) → 거래 모드 매핑 (2026-06-30)."""

    def test_swing_mode_uses_swing_yaml_and_mainnet(self) -> None:
        from scripts.live_run import _resolve_run_mode
        mc = _resolve_run_mode("swing")
        assert "swing_mainnet.yaml" in mc["production_yaml"]
        assert mc["broker_default"] == "bitget-mainnet"
        # 돌파 채널청산 sweep 강제 + 죽은피드 타이머평가 기본
        assert mc["set_env"]["SWING_CHANNEL_SWEEP"] == "1"
        assert mc["setdefault_env"]["SWING_EVAL_TIMER_SEC"] == "60"

    def test_swing_log_dir_is_discovered_by_swing_page(self) -> None:
        # 버튼-swing WAL 이 /swing 라이브 윈도우(SWING_LIVE_LOG_DIRS)에 잡혀야 함
        from scripts.live_run import _resolve_run_mode
        from src.dashboard.swing_live import SWING_LIVE_LOG_DIRS
        assert _resolve_run_mode("swing")["log_dir"] in SWING_LIVE_LOG_DIRS

    def test_airborne_mode_uses_production_yaml_and_clears_swing_env(self) -> None:
        from scripts.live_run import _resolve_run_mode
        mc = _resolve_run_mode("airborne")
        assert "production.yaml" in mc["production_yaml"]
        assert mc["broker_default"] is None
        # 스윙 전용 env 정리 — airborne 동작(타이머평가) 안 바뀌게
        assert "SWING_CHANNEL_SWEEP" in mc["pop_env"]
        assert "SWING_EVAL_TIMER_SEC" in mc["pop_env"]

    def test_unknown_mode_defaults_to_airborne(self) -> None:
        from scripts.live_run import _resolve_run_mode
        assert _resolve_run_mode("")["production_yaml"] == _resolve_run_mode("airborne")["production_yaml"]


class TestRunModeMutualExclusion:
    """한 모드 가동 중 다른 모드 시작 거부 (같은 bitget 계좌 충돌 차단)."""

    def test_mode_passes_through_and_blocks_other_mode(
        self, client_with_controller: tuple[TestClient, RunController],
    ) -> None:
        client, _ = client_with_controller
        # 스윙 시작 → running, request_params.mode=swing 노출
        resp = client.post("/api/run/start", json={"mode": "swing"})
        assert resp.status_code == 200 and resp.json()["ok"] is True
        st = client.get("/api/run/status").json()
        assert st["request_params"].get("mode") == "swing"
        # 가동 중 에어본 시작 → 422 already running (상호배제)
        resp2 = client.post("/api/run/start", json={"mode": "airborne"})
        assert resp2.status_code == 422
        assert resp2.json()["ok"] is False
        client.post("/api/run/stop")
