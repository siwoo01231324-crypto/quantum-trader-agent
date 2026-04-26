"""Dry-run tests for kill-switch gate + 3 auto triggers + CLI."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ops.cli import main as cli_main
from src.ops.kill_switch import KillSwitch, KillSwitchTripped
from src.ops.triggers import (
    ApiErrorRateTrigger,
    ApiErrorTrigger,
    DrawdownTrigger,
    FillAnomalyTrigger,
)


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


# --- DrawdownTrigger (original starting_equity-based cases, preserved) ---

def test_drawdown_trigger_trips_below_limit():
    ks = KillSwitch(dry_run=True)
    t = DrawdownTrigger(kill=ks, limit=-0.03, starting_equity=1_000_000)
    assert t.update(990_000) is False  # -1%, ok
    assert t.update(960_000) is True   # peak=1_000_000, DD=-4%, trip
    assert ks.tripped is True


def test_drawdown_trigger_ignores_zero_starting_equity():
    ks = KillSwitch(dry_run=True)
    t = DrawdownTrigger(kill=ks, limit=-0.03, starting_equity=0.0)
    # first update sets peak=500_000; no drop -> no trip
    assert t.update(500_000) is False
    assert ks.tripped is False


# --- DrawdownTrigger (peak tracking — new) ---

def test_drawdown_peak_tracking():
    """equity 100 -> 110 (peak=110) -> 106.6 (DD > 3%) -> trip"""
    ks = KillSwitch(dry_run=True)
    t = DrawdownTrigger(kill=ks, limit=-0.03, starting_equity=100.0)
    assert t.update(100.0) is False   # peak=100
    assert t.update(110.0) is False   # peak updated to 110
    # 110 * 0.97 = 106.7; use 106.6 to ensure dd < -0.03 (strictly below limit)
    assert t.update(106.6) is True
    assert ks.tripped is True


def test_drawdown_below_starting_no_peak_increase():
    """equity 100 -> 97 (starting=100, peak=100, DD=-3%) -> trip"""
    ks = KillSwitch(dry_run=True)
    t = DrawdownTrigger(kill=ks, limit=-0.03, starting_equity=100.0)
    assert t.update(97.0) is True
    assert ks.tripped is True


# --- ApiErrorTrigger (legacy, preserved) ---

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


# --- ApiErrorRateTrigger (new) ---

def test_api_error_rate_below_min_samples():
    """19건 전부 오류여도 min_samples=20 미달 -> trip 미발생"""
    ks = KillSwitch(dry_run=True)
    t = ApiErrorRateTrigger(kill=ks, window_seconds=300.0, error_rate_threshold=0.05, min_samples=20)
    base = 1000.0
    for i in range(19):
        result = t.record(is_error=True, ts=base + i)
        assert result is False
    assert ks.tripped is False


def test_api_error_rate_above_threshold():
    """100건 중 6건 오류 (6%) > 5% threshold, min_samples=20 충족 -> trip"""
    ks = KillSwitch(dry_run=True)
    t = ApiErrorRateTrigger(kill=ks, window_seconds=300.0, error_rate_threshold=0.05, min_samples=20)
    base = 1000.0
    # 94건 성공 먼저
    for i in range(94):
        t.record(is_error=False, ts=base + i)
    assert ks.tripped is False
    # 6건 오류 추가 -> total=100, errors=6, rate=0.06 > 0.05
    for i in range(6):
        t.record(is_error=True, ts=base + 94 + i)
    assert ks.tripped is True


def test_api_error_rate_window_expiry():
    """윈도우 밖 오래된 이벤트는 자동 제거 -> 오래된 오류는 rate 계산에서 제외"""
    ks = KillSwitch(dry_run=True)
    # min_samples=10 으로 설정해 초기 5건만으로는 trip 되지 않도록 함
    t = ApiErrorRateTrigger(kill=ks, window_seconds=10.0, error_rate_threshold=0.05, min_samples=10)
    # t=0~4: 5건 오류 — min_samples=10 미달이라 trip 없음
    for i in range(5):
        result = t.record(is_error=True, ts=float(i))
        assert result is False
    # t=20~24: 5건 성공 — 이전 오류(t=0~4)는 window(10s) 만료로 제거됨
    # 윈도우 내: 성공 5건만 남아 rate=0 -> trip 없음
    for i in range(5):
        t.record(is_error=False, ts=20.0 + i)
    assert ks.tripped is False


# --- FillAnomalyTrigger (original) ---

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


def test_fill_anomaly_dump_to_file(tmp_path: Path):
    """dump_path 설정 -> 5건 burst -> 파일 생성 + JSONL 1줄"""
    dump_file = tmp_path / "fills" / "dump.jsonl"
    ks = KillSwitch(dry_run=True)
    t = FillAnomalyTrigger(
        kill=ks,
        window_seconds=1.0,
        burst_threshold=5,
        dump_path=dump_file,
    )
    base = 2000.0
    for i in range(4):
        t.record_fill("BTC", ts=base + i * 0.1)
    result = t.record_fill("BTC", ts=base + 0.4)
    assert result is True
    assert ks.tripped is True
    assert dump_file.exists()
    lines = dump_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["trigger"] == "fill_anomaly"
    assert record["symbol"] == "BTC"
    assert len(record["fills_in_window"]) == 5


# --- History & CLI ---

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
