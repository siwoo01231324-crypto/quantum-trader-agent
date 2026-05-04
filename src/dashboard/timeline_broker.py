"""In-process pub/sub for the timeline WebSocket fan-out (#181).

가입자별 bounded `asyncio.Queue` 를 보유한다. `publish()` 시 큐가 가득 차면
가장 오래된 항목을 drop 후 새 항목을 push (drop-oldest back-pressure 정책).

WAL → WebSocket 사이의 단일 fan-out 지점이며, 단일 프로세스(uvicorn worker 1개)
환경에서만 유효하다. 분산 운영은 본 이슈 범위 밖.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_MAXSIZE = 100


class TimelineBroker:
    """단순 in-memory 브로커. asyncio 와 동기 컨텍스트 모두에서 publish 가능."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._dropped_total = 0

    def subscribe(self, maxsize: int = DEFAULT_QUEUE_MAXSIZE) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    def publish(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            self._put_drop_oldest(q, event)

    def _put_drop_oldest(self, q: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
        while True:
            try:
                q.put_nowait(event)
                return
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    self._dropped_total += 1
                except asyncio.QueueEmpty:
                    return  # 다른 컨슈머가 비웠을 가능성 — 다음 루프에서 put 재시도
                # 다음 루프에서 put_nowait 재시도

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def dropped_total(self) -> int:
        return self._dropped_total
