"""Dry-run tests for kill-switch gate + 3 auto triggers + CLI."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ops.cli import main as cli_main
from src.ops.kill_switch import KillSwitch, KillSwitchTripped
from src.ops.triggers import ApiErrorTrigger, DrawdownTrigger, FillAnomalyTrigger


def test_gate_allows_when_not_tripped():
    ks = KillSwitch(dry_run=True)
    assert ks.allow_order() is True
    ks.assert_allow_order()


def test_gate_blocks_new_orders_when_tripped():
    ks = KillSwitch(dry_run=True)
    ks.trip(reason="test", source="manual:test")
    assert ks.tripped is True
    assert ks.allow_order() is False
    with pytest.raises(KillSwitchTripped):
        ks.assert_allow_order()


def test_gate_allows_liquidation_when_tripped():
    ks = KillSwitch(dry_run=True)
    ks.trip(reason="test", source="manual:test")
    assert ks.allow_order(liquidation=True) is True


def test_release_restores_gate():
    ks = KillSwitch(dry_run=True)
    ks.trip(reason="test", source="manual:test")
    ks.release(operator="op1")
    assert ks.tripped is False
    assert ks.allow_order() is True


def test_drawdown_trigger_trips_below_limit():
    ks = KillSwitch(dry_run=True)
    t = DrawdownTrigger(kill=ks, limit=-0.03, starting_equity=1_000_000)
    assert t.update(990_000) is False  # -1%, ok
    assert t.update(960_000) is True   # -4%, trip
    assert ks.tripped is True


def test_drawdown_trigger_ignores_zero_starting_equity():
    ks = KillSwitch(dry_run=True)
    t = DrawdownTrigger(kill=ks, limit=-0.03, starting_equity=0.0)
    assert t.update(500_000) is False
    assert ks.tripped is False


def test_api_error_trigger_consecutive():
    ks = KillSwitch(dry_run=True)
    t = ApiErrorTrigger(kill=ks, threshold=3)
    assert t.record_error() is False
    assert t.record_error() is False
    t.record_success()  # reset
    assert t.record_error() is False
    assert t.record_error() is False
    assert t.record_error() is True
    assert ks.tripped is True


def test_fill_anomaly_burst():
    ks = KillSwitch(dry_run=True)
    t = FillAnomalyTrigger(kill=ks, window_seconds=1.0, burst_threshold=5)
    base = 1000.0
    for i in range(4):
        assert t.record_fill("AAPL", ts=base + i * 0.1) is False
    assert t.record_fill("AAPL", ts=base + 0.4) is True
    assert ks.tripped is True


def test_fill_anomaly_window_slides():
    ks = KillSwitch(dry_run=True)
    t = FillAnomalyTrigger(kill=ks, window_seconds=1.0, burst_threshold=3)
    # Spaced > 1s apart - never trips
    for i in range(10):
        assert t.record_fill("AAPL", ts=1000.0 + i * 2.0) is False
    assert ks.tripped is False


def test_history_records_all_events():
    ks = KillSwitch(dry_run=True)
    ks.trip(reason="r1", source="auto:dd")
    ks.trip(reason="r2", source="auto:api")
    assert len(ks.history()) == 2
    assert ks.last_event().reason == "r2"


def test_cli_kill_release_status_roundtrip(tmp_path: Path, capsys):
    state = tmp_path / "kill.json"

    rc = cli_main(["--state", str(state), "kill", "--reason", "drill", "--operator", "alice"])
    assert rc == 0
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["tripped"] is True
    assert data["events"][0]["reason"] == "drill"

    rc = cli_main(["--state", str(state), "status"])
    assert rc == 0

    rc = cli_main(["--state", str(state), "release", "--operator", "alice"])
    assert rc == 0
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["tripped"] is False
    assert data["events"][-1]["type"] == "release"
