"""Bitget WS 앱레벨 keepalive — 30~60초 false-disconnect 해결 (2026-06-10).

Bitget WS 는 클라이언트가 ~25-30초마다 텍스트 ``"ping"`` 을 보내고 서버가
``"pong"`` 으로 답하는 *앱레벨* heartbeat 를 요구한다. ``websockets`` 라이브러리의
``ping_interval`` 은 *프로토콜* ping frame 을 보내는데 Bitget 이 이를 pong 하지
않아 ``ping_timeout`` 마다 *멀쩡한* 연결을 false-close → 30~60초 주기 재접속 폭주.
운영 2026-06-09/10: 봉처리 루프 정지(거래 0) + synthetic SL 장님(−22% 방치)의
근본 원인.

대책 (데이터 안정화 Phase 1):
  - ``websockets.connect(..., ping_interval=None)`` — 프로토콜 ping 비활성화
  - ``app_level_heartbeat`` — 앱레벨 ``"ping"`` 주기 전송 (Bitget 이 pong)
  - 읽기 루프는 ``is_keepalive_frame(raw)`` 로 ``"pong"``/``"ping"`` 프레임 skip

재연결 자체는 호출측(producer 루프 / async_ws)이 담당 — 본 모듈은 keepalive 만.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

# Bitget 권장 keepalive 주기는 30초 — 여유 두고 25초마다 전송.
PING_INTERVAL_SEC: float = 25.0
PING_PAYLOAD: str = "ping"


def is_keepalive_frame(raw: Any) -> bool:
    """수신 raw 가 Bitget keepalive 프레임(``"pong"``/``"ping"``)인가 — 파싱 skip 용.

    Bitget 은 앱레벨 ping 에 평문 ``"pong"`` 으로 답한다(JSON 아님). json.loads
    실패로 warning 이 찍히던 noise 제거 + 명시적 skip.
    """
    if not isinstance(raw, (str, bytes)):
        return False
    s = raw.decode() if isinstance(raw, bytes) else raw
    return s.strip().strip('"') in ("pong", "ping")


async def app_level_heartbeat(
    ws: Any,
    *,
    interval: float = PING_INTERVAL_SEC,
    stop_check: Callable[[], bool] | None = None,
) -> None:
    """``interval`` 초마다 ``ws`` 로 ``"ping"`` 전송. ws 종료/취소 시 조용히 끝.

    Args:
        ws: 연결된 websocket (``send`` coroutine 보유).
        interval: ping 주기(초). 기본 25 (Bitget 30초 한도 여유).
        stop_check: True 반환 시 즉시 종료(예: feed 의 ``_closed`` 플래그).
    """
    try:
        while True:
            await asyncio.sleep(interval)
            if stop_check is not None and stop_check():
                return
            await ws.send(PING_PAYLOAD)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — ws 닫힘/전송 실패 → 종료, 재연결은 호출측
        return
