from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.live.types import WALEvent, WALCorruption
from src.live.wal import WAL, WALWriteFailed, replay


def _make_event(n: int) -> WALEvent:
    return WALEvent(
        ts=f"2026-04-25T09:00:0{n}.000000+00:00",
        event_type="order_submitted",
        schema_version=1,
        payload={"client_order_id": f"cid-{n}"},
    )


def test_wal_write_replay_round_trip(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal = WAL(wal_path)
    events_in = [_make_event(i) for i in range(3)]
    for e in events_in:
        wal.write(e)

    events_out, corruptions = replay(wal_path)

    assert len(events_out) == 3
    assert corruptions == []
    assert events_out == events_in


def test_wal_replay_truncated_last_line(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal = WAL(wal_path)
    for i in range(2):
        wal.write(_make_event(i))

    # Append truncated (incomplete) JSON without newline
    with open(wal_path, "a", encoding="utf-8") as f:
        f.write('{"ts":"2026-04-25","event_type":"order_submitted","schema_version":1,"payload":{"x"')

    events, corruptions = replay(wal_path)

    assert len(events) == 2
    assert len(corruptions) == 1


def test_wal_replay_invalid_json(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal = WAL(wal_path)
    wal.write(_make_event(0))

    with open(wal_path, "a", encoding="utf-8") as f:
        f.write('{"ts":\n')

    wal.write(_make_event(1))

    events, corruptions = replay(wal_path)

    assert len(events) == 2
    assert len(corruptions) == 1


def test_wal_replay_empty_lines(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal = WAL(wal_path)
    wal.write(_make_event(0))

    with open(wal_path, "a", encoding="utf-8") as f:
        f.write("\n\n")

    wal.write(_make_event(1))

    events, corruptions = replay(wal_path)

    assert len(events) == 2
    assert len(corruptions) == 0


def test_wal_replay_bom(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal = WAL(wal_path)
    wal.write(_make_event(0))

    # Write BOM line
    bom_line = "﻿" + json.dumps({"ts": "2026-04-25T00:00:00+00:00", "event_type": "order_submitted", "schema_version": 1, "payload": {}}) + "\n"
    with open(wal_path, "a", encoding="utf-8") as f:
        f.write(bom_line)

    wal.write(_make_event(1))

    events, corruptions = replay(wal_path)

    assert len(events) == 2
    assert len(corruptions) == 1
    assert corruptions[0].error == "unexpected BOM"


def test_wal_replay_unsupported_schema_version(tmp_path):
    wal_path = tmp_path / "wal.jsonl"

    with open(wal_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-04-25T00:00:00+00:00", "event_type": "order_submitted", "schema_version": 2, "payload": {}}) + "\n")

    events, corruptions = replay(wal_path)

    assert len(events) == 0
    assert len(corruptions) == 1
    assert "unsupported schema_version=2" in corruptions[0].error


def test_wal_replay_missing_file(tmp_path):
    events, corruptions = replay(tmp_path / "nonexistent.jsonl")

    assert events == []
    assert corruptions == []


def test_wal_write_io_error_raises(tmp_path, monkeypatch):
    wal_path = tmp_path / "wal.jsonl"
    wal = WAL(wal_path)

    original_open = open

    def bad_open(*args, **kwargs):
        if args and str(wal_path) in str(args[0]):
            raise OSError("disk full")
        return original_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", bad_open)

    with pytest.raises(WALWriteFailed):
        wal.write(_make_event(0))


def test_wal_replay_empty_file(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal_path.touch()

    events, corruptions = replay(wal_path)

    assert events == []
    assert corruptions == []
