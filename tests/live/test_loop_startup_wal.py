"""WAL startup heartbeat events test (#216 US-004).

Validates ``emit_startup_events`` writes ``run_started`` (always) and
``session_open`` (krx schedule + gate ran). Replays the resulting WAL file to
ensure both events are persisted, fsynced, and parseable.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.live.loop import ShadowConfig, emit_startup_events
from src.live.wal import WAL, replay
from src.universe.krx_calendar import KST


def _make_config(tmp_path: Path, schedule: str) -> ShadowConfig:
    return ShadowConfig(
        symbols=["005930", "035720"],
        wal_path=tmp_path / "run-001" / "wal.jsonl",
        lock_path=tmp_path / "run-001" / ".live_loop.lock",
        initial_balance=Decimal("100000"),
        production_yaml=tmp_path / "missing.yaml",
        broker_mode="kis-paper-shadow",
        feed_mode="auto",
        schedule=schedule,
    )


class TestEmitStartupEvents:

    def test_run_started_written_always(self, tmp_path):
        config = _make_config(tmp_path, "always")
        wal = WAL(config.wal_path)
        emit_startup_events(wal, config, gate_resumed_at=None)

        events, corruptions = replay(config.wal_path)
        assert corruptions == []
        assert len(events) == 1
        assert events[0].event_type == "run_started"
        p = events[0].payload
        assert p["run_id"] == "run-001"
        assert p["broker"] == "kis-paper-shadow"
        assert p["feed"] == "auto"
        assert p["symbols"] == ["005930", "035720"]
        assert p["schedule"] == "always"
        assert "wal_path" in p

    def test_run_started_plus_session_open_for_krx(self, tmp_path):
        config = _make_config(tmp_path, "krx")
        wal = WAL(config.wal_path)
        gate_at = datetime(2026, 5, 11, 9, 0).replace(tzinfo=KST)
        emit_startup_events(wal, config, gate_resumed_at=gate_at)

        events, corruptions = replay(config.wal_path)
        assert corruptions == []
        assert [e.event_type for e in events] == ["run_started", "session_open"]
        session = events[1]
        assert session.payload["date"] == "2026-05-11"
        assert session.payload["kst_open"].startswith("2026-05-11T09:00:00")

    def test_session_open_skipped_for_always_schedule(self, tmp_path):
        config = _make_config(tmp_path, "always")
        wal = WAL(config.wal_path)
        gate_at = datetime(2026, 5, 11, 9, 0).replace(tzinfo=KST)
        emit_startup_events(wal, config, gate_resumed_at=gate_at)

        events, _ = replay(config.wal_path)
        assert [e.event_type for e in events] == ["run_started"]

    def test_session_open_skipped_when_no_gate_timestamp(self, tmp_path):
        # schedule='krx' 인데 gate 가 None 반환한 경우 (예: stub fake_wait → None).
        config = _make_config(tmp_path, "krx")
        wal = WAL(config.wal_path)
        emit_startup_events(wal, config, gate_resumed_at=None)

        events, _ = replay(config.wal_path)
        assert [e.event_type for e in events] == ["run_started"]
