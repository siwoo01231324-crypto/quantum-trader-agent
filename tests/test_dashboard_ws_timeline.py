"""WebSocket /ws/timeline tests (#181).

신호→메타라벨러→주문→체결 4단계 이벤트의 실시간 fan-out.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard.app import DashboardState, create_app
from src.dashboard.timeline_broker import TimelineBroker
from src.dashboard.timeline_events import (
    EVENT_FILL_RECEIVED,
    EVENT_METALABELER_DECISION,
    EVENT_ORDER_PLACED,
    EVENT_SIGNAL_EMITTED,
)
from src.live.types import WALEvent
from src.live.wal import WAL


# ---------------------------------------------------------------------------
# TimelineBroker — back-pressure (drop-oldest) unit tests
# ---------------------------------------------------------------------------

class TestTimelineBroker:
    def test_subscribe_returns_queue(self) -> None:
        b = TimelineBroker()
        q = b.subscribe()
        assert isinstance(q, asyncio.Queue)
        assert b.subscriber_count == 1

    def test_unsubscribe(self) -> None:
        b = TimelineBroker()
        q = b.subscribe()
        b.unsubscribe(q)
        assert b.subscriber_count == 0

    def test_publish_delivers_to_all_subscribers(self) -> None:
        b = TimelineBroker()
        q1 = b.subscribe()
        q2 = b.subscribe()
        b.publish({"event_type": "signal_emitted", "payload": {"x": 1}})
        assert q1.qsize() == 1
        assert q2.qsize() == 1
        ev1 = q1.get_nowait()
        assert ev1["event_type"] == "signal_emitted"

    def test_publish_drops_oldest_on_overflow(self) -> None:
        b = TimelineBroker()
        q = b.subscribe(maxsize=3)
        for i in range(10):
            b.publish({"event_type": "signal_emitted", "payload": {"i": i}})
        # 큐가 3 건만 보유 (가장 최근 3건)
        assert q.qsize() == 3
        items = [q.get_nowait() for _ in range(3)]
        # drop oldest → 마지막 3건이 남음 (i=7,8,9)
        assert [x["payload"]["i"] for x in items] == [7, 8, 9]
        assert b.dropped_total == 7

    def test_publish_no_subscribers_does_not_raise(self) -> None:
        b = TimelineBroker()
        b.publish({"event_type": "signal_emitted"})  # no-op


# ---------------------------------------------------------------------------
# WebSocket endpoint — replay + live + 4 event types
# ---------------------------------------------------------------------------

@pytest.fixture()
def state() -> DashboardState:
    return DashboardState()


@pytest.fixture()
def client(state: DashboardState) -> TestClient:
    app = create_app(state)
    return TestClient(app)


class TestWSTimelineLive:
    def test_ws_connect_succeeds(self, client: TestClient) -> None:
        with client.websocket_connect("/ws/timeline?replay=0") as ws:
            # initial replay phase: empty → first message is sentinel "live_ready"
            msg = ws.receive_json()
            assert msg["phase"] == "live_ready"

    def test_ws_live_publish(self, client: TestClient, state: DashboardState) -> None:
        with client.websocket_connect("/ws/timeline?replay=0") as ws:
            assert ws.receive_json()["phase"] == "live_ready"
            state.timeline_broker.publish({
                "ts": "2026-05-04T10:00:00+00:00",
                "event_type": EVENT_SIGNAL_EMITTED,
                "schema_version": 1,
                "payload": {"symbol": "005930", "direction": "BUY"},
            })
            ev = ws.receive_json()
            assert ev["event_type"] == EVENT_SIGNAL_EMITTED
            assert ev["payload"]["symbol"] == "005930"

    def test_ws_4_event_types_pass_through(self, client: TestClient, state: DashboardState) -> None:
        types_ = [
            EVENT_SIGNAL_EMITTED,
            EVENT_METALABELER_DECISION,
            EVENT_ORDER_PLACED,
            EVENT_FILL_RECEIVED,
        ]
        with client.websocket_connect("/ws/timeline?replay=0") as ws:
            assert ws.receive_json()["phase"] == "live_ready"
            for t in types_:
                state.timeline_broker.publish({
                    "ts": "2026-05-04T10:00:00+00:00",
                    "event_type": t,
                    "schema_version": 1,
                    "payload": {},
                })
            received = [ws.receive_json()["event_type"] for _ in types_]
            assert received == types_


class TestWSTimelineReplay:
    def test_ws_replay_empty_when_no_wal(self, client: TestClient) -> None:
        with client.websocket_connect("/ws/timeline?replay=100") as ws:
            msg = ws.receive_json()
            # WAL 미설정 → replay 즉시 live_ready
            assert msg["phase"] == "live_ready"
            assert msg.get("replayed", 0) == 0

    def test_ws_replay_last_n_from_wal(self, tmp_path: Path) -> None:
        wal_path = tmp_path / "wal.jsonl"
        wal = WAL(wal_path)
        for i in range(150):
            wal.write(WALEvent(
                ts=f"2026-05-04T10:00:{i:02d}+00:00",
                event_type=EVENT_SIGNAL_EMITTED,
                payload={"i": i},
            ))
        s = DashboardState()
        s.wal_path = wal_path
        app = create_app(s)
        c = TestClient(app)
        with c.websocket_connect("/ws/timeline?replay=100") as ws:
            replay_events = []
            while True:
                msg = ws.receive_json()
                if msg.get("phase") == "live_ready":
                    assert msg["replayed"] == 100
                    break
                replay_events.append(msg)
            assert len(replay_events) == 100
            # 마지막 100건 = i=50..149
            assert replay_events[0]["payload"]["i"] == 50
            assert replay_events[-1]["payload"]["i"] == 149


class TestWSTimelineDisconnect:
    def test_disconnect_unsubscribes(self, client: TestClient, state: DashboardState) -> None:
        with client.websocket_connect("/ws/timeline?replay=0") as ws:
            assert ws.receive_json()["phase"] == "live_ready"
            assert state.timeline_broker.subscriber_count == 1
        # ctx 종료 = 클라 disconnect → unsubscribe 까지 잠시 시간 필요할 수 있음
        # FastAPI TestClient 는 컨텍스트 종료 시 close 신호 동기 전달
        # 짧은 polling
        import time
        deadline = time.time() + 1.0
        while time.time() < deadline and state.timeline_broker.subscriber_count != 0:
            time.sleep(0.02)
        assert state.timeline_broker.subscriber_count == 0


# ---------------------------------------------------------------------------
# WAL observer 훅 — fan-out
# ---------------------------------------------------------------------------

class TestWALObserver:
    def test_wal_calls_observer_after_write(self, tmp_path: Path) -> None:
        captured: list[WALEvent] = []
        wal = WAL(tmp_path / "w.jsonl", observer=captured.append)
        ev = WALEvent(ts="t", event_type=EVENT_ORDER_PLACED, payload={"x": 1})
        wal.write(ev)
        assert captured == [ev]

    def test_wal_observer_exception_does_not_break_write(self, tmp_path: Path) -> None:
        def boom(_ev: WALEvent) -> None:
            raise RuntimeError("subscriber crashed")
        path = tmp_path / "w.jsonl"
        wal = WAL(path, observer=boom)
        wal.write(WALEvent(ts="t", event_type="x"))
        # WAL 파일은 정상 기록
        line = path.read_text(encoding="utf-8").strip()
        assert json.loads(line)["event_type"] == "x"

    def test_wal_default_no_observer(self, tmp_path: Path) -> None:
        # observer 미지정 시 기존 동작 유지 — 회귀 방지
        wal = WAL(tmp_path / "w.jsonl")
        wal.write(WALEvent(ts="t", event_type="x"))
        # 예외 없이 통과
