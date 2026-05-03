"""Unit tests for scripts/live_report.py — mock WAL 5건 입력 → AC 카운트 정확."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.live_report import (
    evaluate_ac,
    evaluate_halt_triggers,
    render_report_md,
    _count_placed,
    _count_filled,
    _distinct_trading_dates,
    _count_fill_missing,
    _count_kill_switch_trips,
    _count_ws_reconnects,
)
from src.live.types import WALEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ev(event_type: str, ts: str = "2026-04-21T10:00:00+00:00", payload: dict | None = None) -> WALEvent:
    return WALEvent(ts=ts, event_type=event_type, schema_version=1, payload=payload or {})


def _make_wal_events() -> list[WALEvent]:
    """5 mock events covering key event types."""
    return [
        _ev("order_acked",        ts="2026-04-21T10:00:00+00:00", payload={"client_order_id": "c1", "broker_order_id": "b1", "status": "FILLED"}),
        _ev("order_filled",       ts="2026-04-21T11:00:00+00:00", payload={"client_order_id": "c1"}),
        _ev("order_acked",        ts="2026-04-22T10:00:00+00:00", payload={"client_order_id": "c2", "broker_order_id": "b2", "status": "FILLED"}),
        _ev("kill_switch_tripped",ts="2026-04-22T12:00:00+00:00"),
        _ev("ws_reconnected",     ts="2026-04-23T09:00:00+00:00"),
    ]


# ---------------------------------------------------------------------------
# Test 1: event counters are accurate
# ---------------------------------------------------------------------------

def test_event_counters():
    events = _make_wal_events()
    assert _count_placed(events) == 2
    assert _count_filled(events) == 1
    assert _count_kill_switch_trips(events) == 1
    assert _count_ws_reconnects(events) == 1
    assert _count_fill_missing(events) == 0


# ---------------------------------------------------------------------------
# Test 2: distinct trading dates
# ---------------------------------------------------------------------------

def test_distinct_trading_dates():
    events = _make_wal_events()
    dates = _distinct_trading_dates(events)
    assert len(dates) == 2  # 2026-04-21 and 2026-04-22


# ---------------------------------------------------------------------------
# Test 3: evaluate_ac with insufficient data → expected FAILs
# ---------------------------------------------------------------------------

def test_evaluate_ac_insufficient():
    events = _make_wal_events()
    ac = evaluate_ac(events, tracking_error_p95=None)

    # Only 2 trading days — AC2 requires 20
    assert ac["AC2_trading_days"]["passed"] is False
    assert ac["AC2_trading_days"]["value"] == 2

    # placed=2, filled=1 → ratio=0.5 < 0.95 AND placed < 100 → AC3 FAIL
    assert ac["AC3_orders"]["passed"] is False

    # kill_switch_trips=1 < 3 → AC5 FAIL
    assert ac["AC5_kill_switch"]["passed"] is False

    # ws_reconnects=1 → AC6 PASS
    assert ac["AC6_ws_reconnect"]["passed"] is True


# ---------------------------------------------------------------------------
# Test 4: evaluate_ac with sufficient data → expected PASSes
# ---------------------------------------------------------------------------

def test_evaluate_ac_sufficient():
    # Build events with 20 trading days, 100+ placed, 100 filled
    events = []
    from datetime import date, timedelta
    base_date = date(2026, 1, 1)
    for i in range(100):
        day = base_date + timedelta(days=i)
        ts = f"{day}T10:00:00+00:00"
        events.append(_ev("order_acked", ts=ts, payload={"client_order_id": f"c{i}", "broker_order_id": f"b{i}", "status": "FILLED"}))
        events.append(_ev("order_filled", ts=ts))

    for j in range(3):
        events.append(_ev("kill_switch_tripped"))
    events.append(_ev("ws_reconnected"))

    ac = evaluate_ac(events, tracking_error_p95=0.003)  # 0.3% < 0.5%

    assert ac["AC2_trading_days"]["passed"] is True
    assert ac["AC3_orders"]["passed"] is True
    assert ac["AC4_tracking_error"]["passed"] is True
    assert ac["AC5_kill_switch"]["passed"] is True
    assert ac["AC6_ws_reconnect"]["passed"] is True


# ---------------------------------------------------------------------------
# Test 5: evaluate_halt_triggers fill_missing threshold
# ---------------------------------------------------------------------------

def test_halt_trigger_fill_missing():
    events = [_ev("fill_anomaly"), _ev("fill_anomaly")]

    result = evaluate_halt_triggers(events, fill_missing_threshold=1)
    assert result["R2_fill_missing"]["triggered"] is True

    result2 = evaluate_halt_triggers(events, fill_missing_threshold=3)
    assert result2["R2_fill_missing"]["triggered"] is False


# ---------------------------------------------------------------------------
# Test 6: render_report_md produces valid markdown with expected sections
# ---------------------------------------------------------------------------

def test_render_report_md():
    events = _make_wal_events()
    ac = evaluate_ac(events)
    halt = evaluate_halt_triggers(events)
    md = render_report_md("2026-04-27", events, ac, halt)

    assert "Phase 2 KIS 모의계좌" in md
    assert "AC Exit Gate" in md
    assert "Halt 트리거" in md
    assert "WAL 이벤트 요약" in md
    assert "order_acked" in md


# ---------------------------------------------------------------------------
# Test 7: live_run.py --help works (CLI parse smoke test)
# ---------------------------------------------------------------------------

def test_live_run_parse_help():
    from scripts.live_run import parse_args
    args = parse_args(["--symbols", "005930,035720", "--broker", "kis-paper-shadow"])
    assert args.symbols == ["005930", "035720"]
    assert args.broker == "kis-paper-shadow"
    assert args.auto_fallback is True


# ---------------------------------------------------------------------------
# Test 8: live_report.py --help works (CLI parse smoke test)
# ---------------------------------------------------------------------------

def test_live_report_parse_args():
    from scripts.live_report import _parse_args
    args = _parse_args(["--wal", "/tmp/test.jsonl", "--date", "2026-04-27"])
    assert args.date == "2026-04-27"
    assert args.wal == "/tmp/test.jsonl"
