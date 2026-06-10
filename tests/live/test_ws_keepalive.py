"""Bitget WS 앱레벨 keepalive 헬퍼 테스트 (데이터 안정화 Phase 1, 2026-06-10)."""
from __future__ import annotations

import asyncio

import pytest

from src.live.ws_keepalive import (
    PING_PAYLOAD,
    app_level_heartbeat,
    is_keepalive_frame,
)


# ── is_keepalive_frame ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("pong", True),
    ("ping", True),
    ('"pong"', True),       # 따옴표 감싼 형태
    (b"pong", True),        # bytes
    ("  pong  ", True),     # 공백
    ('{"arg": {"channel": "trade"}}', False),  # JSON 메시지
    ("", False),
    (123, False),           # non-str/bytes
    ({"pong": 1}, False),   # dict
])
def test_is_keepalive_frame(raw, expected):
    assert is_keepalive_frame(raw) is expected


# ── app_level_heartbeat ─────────────────────────────────────────────────────

class _SpyWS:
    def __init__(self, fail_after: int | None = None):
        self.sent: list[str] = []
        self._fail_after = fail_after

    async def send(self, payload: str) -> None:
        if self._fail_after is not None and len(self.sent) >= self._fail_after:
            raise ConnectionError("ws closed")
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_heartbeat_sends_ping_periodically():
    ws = _SpyWS()
    task = asyncio.create_task(app_level_heartbeat(ws, interval=0.01))
    await asyncio.sleep(0.035)  # ~3 ticks
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ws.sent  # 최소 1회 ping 전송
    assert all(p == PING_PAYLOAD for p in ws.sent)


@pytest.mark.asyncio
async def test_heartbeat_stops_on_stop_check():
    ws = _SpyWS()
    stop = False
    task = asyncio.create_task(
        app_level_heartbeat(ws, interval=0.01, stop_check=lambda: stop)
    )
    await asyncio.sleep(0.025)
    stop = True
    await asyncio.sleep(0.02)
    # stop_check True 이후엔 추가 전송 없이 task 종료
    await asyncio.wait_for(task, timeout=0.1)
    assert task.done()


@pytest.mark.asyncio
async def test_heartbeat_returns_silently_on_send_failure():
    ws = _SpyWS(fail_after=1)  # 2번째 send 에서 ws 닫힘
    # 예외 전파 없이 조용히 종료 (재연결은 호출측 담당)
    await asyncio.wait_for(
        app_level_heartbeat(ws, interval=0.01), timeout=0.2
    )
    assert len(ws.sent) == 1
