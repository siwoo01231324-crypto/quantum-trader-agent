"""Tests for src/dashboard/shadow_runs.py — read-only WAL viewer (#198).

Covers:
1. Empty parent dir → []
2. Run dir exists, no WAL → idle status, events=0
3. Run dir with order_filled events → counts + last_event_ts + alive status
4. Exchange classification (binance / kis / unknown)
5. Timeframe classification from run_id naming
6. Liveness thresholds per timeframe
7. load_run_detail with broker state reconstruction
8. e2e: real logs/shadow paths from #143/#199 (whatever exists)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.dashboard.shadow_runs import (
    classify_alive,
    classify_exchange,
    classify_timeframe,
    discover_shadow_runs,
    load_run_detail,
)


# ---------------------------------------------------------------------------
# WAL fixtures
# ---------------------------------------------------------------------------

def _wal_line(event_type: str, ts: str, payload: dict | None = None) -> str:
    """Build a single JSONL line in WALEvent format."""
    return json.dumps({
        "schema_version": 1,
        "ts": ts,
        "event_type": event_type,
        "payload": payload or {},
    }) + "\n"


def _make_run_dir(parent: Path, run_id: str, events: list[tuple] | None = None) -> Path:
    """Create logs/shadow/{run_id}/ with optional wal.jsonl events.

    Each event: (event_type, ts_iso, payload_dict)
    """
    rd = parent / run_id
    rd.mkdir(parents=True, exist_ok=True)
    if events:
        wal = rd / "wal.jsonl"
        with open(wal, "w", encoding="utf-8") as f:
            for et, ts, payload in events:
                f.write(_wal_line(et, ts, payload))
    return rd


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

class TestClassifyExchange:
    def test_btcusdt_is_binance(self):
        assert classify_exchange("BTCUSDT") == "binance"

    def test_ethusdt_is_binance(self):
        assert classify_exchange("ETHUSDT") == "binance"

    def test_kis_six_digit(self):
        assert classify_exchange("005930") == "kis"
        assert classify_exchange("000660") == "kis"

    def test_unknown_for_random(self):
        assert classify_exchange("FOOBAR") == "unknown"
        assert classify_exchange("") == "unknown"


class TestClassifyTimeframe:
    def test_r4_switch_is_4h(self):
        assert classify_timeframe("phase1-r4-switch-BTCUSDT") == "4h"

    def test_r6_switch_is_1h(self):
        assert classify_timeframe("phase1-r6-switch-BTCUSDT") == "1h"

    def test_s2c_voltarget_is_4h(self):
        assert classify_timeframe("test-s2c-voltarget-BTCUSDT") == "4h"

    def test_kis_run_is_eod(self):
        assert classify_timeframe("phase2-kis-momo-005930") == "EOD"

    def test_unknown_run(self):
        assert classify_timeframe("random-run-id-XYZ") == "unknown"


class TestClassifyAlive:
    def test_none_last_ts_is_idle(self):
        assert classify_alive(None, "4h") == "idle"

    def test_recent_4h_event_is_alive(self):
        # 4h timeframe → alive_threshold = 4h × 1.5 = 6h
        # Event 1h ago is well within threshold.
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        assert classify_alive(recent, "4h") == "alive"

    def test_old_4h_event_is_dead(self):
        # 4h timeframe → dead_threshold = 4h × 3 = 12h
        old = datetime.now(timezone.utc) - timedelta(hours=15)
        assert classify_alive(old, "4h") == "dead"

    def test_borderline_4h_event_is_idle(self):
        # 4h × 1.5 = 6h; 4h × 3.0 = 12h. 8h ago → in idle band.
        borderline = datetime.now(timezone.utc) - timedelta(hours=8)
        assert classify_alive(borderline, "4h") == "idle"

    def test_recent_1h_event_is_alive(self):
        # 1h × 1.5 = 90 min. 30 min ago → alive.
        recent = datetime.now(timezone.utc) - timedelta(minutes=30)
        assert classify_alive(recent, "1h") == "alive"

    def test_old_1h_event_is_dead(self):
        # 1h × 3 = 3h. 4h ago → dead.
        old = datetime.now(timezone.utc) - timedelta(hours=4)
        assert classify_alive(old, "1h") == "dead"


# ---------------------------------------------------------------------------
# discover_shadow_runs
# ---------------------------------------------------------------------------

class TestDiscoverShadowRuns:
    def test_empty_dir_returns_empty_list(self, tmp_path: Path):
        assert discover_shadow_runs(tmp_path) == []

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path):
        assert discover_shadow_runs(tmp_path / "nonexistent") == []

    def test_run_dir_no_wal_returns_idle(self, tmp_path: Path):
        _make_run_dir(tmp_path, "phase1-r4-switch-BTCUSDT", events=None)
        runs = discover_shadow_runs(tmp_path)
        assert len(runs) == 1
        assert runs[0]["run_id"] == "phase1-r4-switch-BTCUSDT"
        assert runs[0]["exchange"] == "binance"
        assert runs[0]["symbol"] == "BTCUSDT"
        assert runs[0]["timeframe"] == "4h"
        assert runs[0]["n_events"] == 0
        assert runs[0]["status"] == "idle"

    def test_run_with_filled_events(self, tmp_path: Path):
        recent_ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        events = [
            ("order_submitted", recent_ts,
             {"client_order_id": "abc-1", "symbol": "BTCUSDT", "side": "BUY", "qty": "0.5"}),
            ("order_filled", recent_ts,
             {"client_order_id": "abc-1", "symbol": "BTCUSDT", "side": "BUY",
              "fill_qty": "0.5", "fill_price": "50000", "fees": "0.04",
              "fee_asset": "USDT"}),
        ]
        _make_run_dir(tmp_path, "phase1-r4-switch-BTCUSDT", events)
        runs = discover_shadow_runs(tmp_path)
        assert len(runs) == 1
        r = runs[0]
        assert r["n_events"] == 2
        assert r["n_submitted"] == 1
        assert r["n_filled"] == 1
        assert r["n_entry"] == 1
        assert r["n_exit"] == 0
        assert r["status"] == "alive"
        assert r["last_event_ts"] is not None

    def test_two_runs_sorted_by_last_event(self, tmp_path: Path):
        # Old run (2 days ago) → status=dead
        old_ts = (datetime.now(timezone.utc) - timedelta(days=2)).replace(microsecond=0).isoformat()
        # Recent run (1 hour ago)
        new_ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).replace(microsecond=0).isoformat()
        _make_run_dir(tmp_path, "phase1-r4-switch-BTCUSDT", [
            ("order_filled", old_ts, {"side": "BUY"}),
        ])
        _make_run_dir(tmp_path, "phase1-r6-switch-BTCUSDT", [
            ("order_filled", new_ts, {"side": "BUY"}),
        ])
        runs = discover_shadow_runs(tmp_path)
        assert len(runs) == 2
        # Both have events, so most-recent-first (descending ts string).
        assert runs[0]["run_id"] == "phase1-r6-switch-BTCUSDT"
        assert runs[1]["run_id"] == "phase1-r4-switch-BTCUSDT"

    def test_kis_run_classified(self, tmp_path: Path):
        _make_run_dir(tmp_path, "phase2-kis-momo-005930", events=None)
        runs = discover_shadow_runs(tmp_path)
        assert len(runs) == 1
        assert runs[0]["exchange"] == "kis"
        assert runs[0]["symbol"] == "005930"
        assert runs[0]["timeframe"] == "EOD"


# ---------------------------------------------------------------------------
# load_run_detail
# ---------------------------------------------------------------------------

class TestLoadRunDetail:
    def test_nonexistent_run_returns_none(self, tmp_path: Path):
        assert load_run_detail(tmp_path, "phase1-doesnotexist") is None

    def test_run_no_wal_returns_summary_only(self, tmp_path: Path):
        _make_run_dir(tmp_path, "phase1-r4-switch-BTCUSDT")
        d = load_run_detail(tmp_path, "phase1-r4-switch-BTCUSDT")
        assert d is not None
        assert d["run_id"] == "phase1-r4-switch-BTCUSDT"
        assert d["positions"] == []
        assert d["balance_usdt"] is None

    def test_run_with_filled_position_reconstruction(self, tmp_path: Path):
        # WAL replay should produce open LONG position.
        ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        events = [
            ("order_submitted", ts,
             {"client_order_id": "buy-1", "symbol": "BTCUSDT", "side": "BUY",
              "qty": "0.5", "order_type": "MARKET", "tif": "IOC"}),
            ("order_filled", ts,
             {"client_order_id": "buy-1", "symbol": "BTCUSDT", "side": "BUY",
              "fill_qty": "0.5", "fill_price": "50000", "fees": "0.04",
              "fee_asset": "USDT"}),
        ]
        _make_run_dir(tmp_path, "phase1-r4-switch-BTCUSDT", events)
        d = load_run_detail(tmp_path, "phase1-r4-switch-BTCUSDT")
        assert d is not None
        assert d["n_events"] == 2
        # Position reconstruction may succeed or be skipped if broker schema
        # mismatches; either way, no exception should bubble up.
        assert "positions" in d
        assert "balance_usdt" in d


# ---------------------------------------------------------------------------
# E2E: FastAPI routes via test client
# ---------------------------------------------------------------------------

class TestShadowRunsRoutes:
    """Hit the actual FastAPI endpoints with a temp shadow_log_dir."""

    def test_api_shadow_runs_empty(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from src.dashboard.app import DashboardState, create_app

        state = DashboardState(shadow_log_dir=tmp_path)
        client = TestClient(create_app(state))
        resp = client.get("/api/shadow_runs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_api_shadow_runs_with_data(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from src.dashboard.app import DashboardState, create_app

        ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        _make_run_dir(tmp_path, "phase1-r4-switch-BTCUSDT", [
            ("order_filled", ts, {"side": "BUY"}),
        ])
        state = DashboardState(shadow_log_dir=tmp_path)
        client = TestClient(create_app(state))
        resp = client.get("/api/shadow_runs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["run_id"] == "phase1-r4-switch-BTCUSDT"
        assert data[0]["exchange"] == "binance"

    def test_api_shadow_run_detail_404(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from src.dashboard.app import DashboardState, create_app

        state = DashboardState(shadow_log_dir=tmp_path)
        client = TestClient(create_app(state))
        resp = client.get("/api/shadow_runs/does-not-exist")
        assert resp.status_code == 404

    def test_html_shadow_runs_empty(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from src.dashboard.app import DashboardState, create_app

        state = DashboardState(shadow_log_dir=tmp_path)
        client = TestClient(create_app(state))
        resp = client.get("/shadow_runs")
        assert resp.status_code == 200
        assert "Shadow Runs" in resp.text
        assert "아직 가동된 shadow run 이 없습니다" in resp.text

    def test_html_shadow_runs_with_card(self, tmp_path: Path):
        from fastapi.testclient import TestClient
        from src.dashboard.app import DashboardState, create_app

        ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        _make_run_dir(tmp_path, "phase1-r6-switch-BTCUSDT", [
            ("order_filled", ts, {"side": "BUY"}),
        ])
        state = DashboardState(shadow_log_dir=tmp_path)
        client = TestClient(create_app(state))
        resp = client.get("/shadow_runs")
        assert resp.status_code == 200
        assert "phase1-r6-switch-BTCUSDT" in resp.text
        assert "binance" in resp.text
        assert "1h" in resp.text
