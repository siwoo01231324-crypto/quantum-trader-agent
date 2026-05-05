"""거래 파이프라인 시작/정지 제어 (#182 단계 2).

대시보드의 [거래 시작]/[정지] 버튼이 호출하는 REST endpoint 의 backend.
이미 띄워진 dashboard 의 event loop 안에서 거래 파이프라인을 background task 로
시작/정지한다. RunController 는 task 핸들과 상태(STOPPED/STARTING/RUNNING/...)
만 보유 — 실제 거래 로직은 외부에서 전달된 callable (보통 _run_pipeline) 이 담당.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# Status 상수 — endpoint 응답에 그대로 노출됨 (대시보드 JS 가 사용)
STATUS_STOPPED = "stopped"
STATUS_STARTING = "starting"
STATUS_RUNNING = "running"
STATUS_STOPPING = "stopping"
STATUS_ERROR = "error"


@dataclass
class RunState:
    """대시보드와 endpoint 가 공유하는 거래 실행 상태."""
    status: str = STATUS_STOPPED
    started_at: str | None = None
    stopped_at: str | None = None
    last_error: str | None = None
    request_params: dict[str, Any] = field(default_factory=dict)


class RunController:
    """거래 파이프라인 task 핸들 + 상태 머신.

    pipeline_factory 는 호출 시 awaitable 을 반환해야 하며, 호출 인자는
    request_params dict (symbols, broker 등). 실제 거래 시작·종료 책임은
    pipeline_factory 안에 있고 RunController 는 task 생명주기만 관리.
    """

    def __init__(
        self,
        pipeline_factory: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._factory = pipeline_factory
        self._task: asyncio.Task | None = None
        self.state = RunState()

    async def start(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.state.status in (STATUS_STARTING, STATUS_RUNNING):
            return {"ok": False, "reason": "already running", "state": self._snapshot()}
        self.state.status = STATUS_STARTING
        self.state.last_error = None
        self.state.request_params = dict(params)
        self.state.started_at = datetime.now(timezone.utc).isoformat()
        try:
            coro = self._factory(self.state.request_params)
            self._task = asyncio.create_task(self._wrap(coro), name="qta-pipeline")
        except Exception as err:  # 자격증명 누락 등 SystemExit 도 포함
            self.state.status = STATUS_ERROR
            self.state.last_error = f"{type(err).__name__}: {err}"
            logger.warning("RunController.start failed: %s", err)
            return {"ok": False, "reason": "factory_failed", "state": self._snapshot()}
        self.state.status = STATUS_RUNNING
        return {"ok": True, "state": self._snapshot()}

    async def _wrap(self, coro: Awaitable[None]) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            logger.info("RunController: pipeline cancelled")
            raise
        except SystemExit as err:
            self.state.status = STATUS_ERROR
            self.state.last_error = f"SystemExit: {err}"
            logger.warning("RunController: pipeline SystemExit %s", err)
        except Exception as err:
            self.state.status = STATUS_ERROR
            self.state.last_error = f"{type(err).__name__}: {err}"
            logger.exception("RunController: pipeline failed")
        else:
            self.state.status = STATUS_STOPPED
            self.state.stopped_at = datetime.now(timezone.utc).isoformat()

    async def stop(self, timeout: float = 5.0) -> dict[str, Any]:
        if self.state.status not in (STATUS_STARTING, STATUS_RUNNING):
            return {"ok": False, "reason": "not running", "state": self._snapshot()}
        self.state.status = STATUS_STOPPING
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        self.state.status = STATUS_STOPPED
        self.state.stopped_at = datetime.now(timezone.utc).isoformat()
        self._task = None
        return {"ok": True, "state": self._snapshot()}

    def _snapshot(self) -> dict[str, Any]:
        return {
            "status": self.state.status,
            "started_at": self.state.started_at,
            "stopped_at": self.state.stopped_at,
            "last_error": self.state.last_error,
            "request_params": dict(self.state.request_params),
        }

    def status(self) -> dict[str, Any]:
        return self._snapshot()
