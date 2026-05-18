"""Dashboard defects from real user testing of the #238 dashboard.

Issue 2 — trade-history / strategy positions must be PERMANENT (cross-run):
  - /api/strategy_positions aggregates across ALL run dirs under log_dir,
    not just the current run's wal_path (it wiped on every new run).
  - /api/trade_history likewise sees every run when state.log_dir is set.
  - _resolve_log_dir falls back to logs/live when nothing else resolves.

Issue 1 — venue-inert visibility:
  - /api/venue_equity_status surfaces SnapshotBuilder.last_equity_status so
    "0 trades because equity unavailable" is no longer silent.
  - the dashboard HTML carries a per-venue indicator element.

Mirrors the WAL-fixture conventions used by tests/test_dashboard_shadow_runs.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard.app import DashboardState, create_app


def _wal_line(event_type: str, ts: str, payload: dict) -> str:
    return json.dumps({
        "schema_version": 1,
        "ts": ts,
        "event_type": event_type,
        "payload": payload,
    }) + "\n"


def _make_run(log_dir: Path, run_id: str, lines: list[str]) -> Path:
    rd = log_dir / run_id
    rd.mkdir(parents=True, exist_ok=True)
    wal = rd / "wal.jsonl"
    wal.write_text("".join(lines), encoding="utf-8")
    return wal


def _buy(strategy_id: str, symbol: str, qty: str, price: str, ts: str) -> str:
    return _wal_line("order_filled", ts, {
        "strategy_id": strategy_id, "symbol": symbol, "side": "buy",
        "qty": qty, "price": price, "fill_qty": qty, "fill_price": price,
    })


# ── Issue 2: cross-run aggregation ─────────────────────────────────────────

class TestStrategyPositionsCrossRun:
    def test_aggregates_across_two_run_dirs(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "live"
        run_a_wal = _make_run(log_dir, "20260101T000000Z", [
            _buy("alpha", "BTCUSDT", "1", "100", "2026-01-01T00:00:01Z"),
        ])
        _make_run(log_dir, "20260102T000000Z", [
            _buy("beta", "ETHUSDT", "2", "50", "2026-01-02T00:00:01Z"),
        ])
        # Current run is A; B is a *previous* run that must still appear.
        state = DashboardState(wal_path=run_a_wal, log_dir=log_dir)
        client = TestClient(create_app(state))
        resp = client.get("/api/strategy_positions")
        assert resp.status_code == 200
        sids = {s["strategy_id"] for s in resp.json()["strategies"]}
        assert "alpha" in sids and "beta" in sids, sids

    def test_current_run_outside_discovered_set_is_unioned(
        self, tmp_path: Path,
    ) -> None:
        log_dir = tmp_path / "live"
        _make_run(log_dir, "20260102T000000Z", [
            _buy("beta", "ETHUSDT", "2", "50", "2026-01-02T00:00:01Z"),
        ])
        # Active wal lives OUTSIDE log_dir's */wal.jsonl glob.
        outside = tmp_path / "elsewhere" / "wal.jsonl"
        outside.parent.mkdir(parents=True)
        outside.write_text(
            _buy("alpha", "BTCUSDT", "1", "100", "2026-01-01T00:00:01Z"),
            encoding="utf-8",
        )
        state = DashboardState(wal_path=outside, log_dir=log_dir)
        client = TestClient(create_app(state))
        sids = {
            s["strategy_id"]
            for s in client.get("/api/strategy_positions").json()["strategies"]
        }
        assert "alpha" in sids and "beta" in sids, sids

    def test_missing_dir_returns_empty_never_500(self, tmp_path: Path) -> None:
        state = DashboardState(log_dir=tmp_path / "does_not_exist")
        client = TestClient(create_app(state))
        resp = client.get("/api/strategy_positions")
        assert resp.status_code == 200
        assert resp.json() == {"available": True, "strategies": []}

    def test_no_state_at_all_returns_empty(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        # Isolate cwd so the logs/live fallback can't pick up the repo's real
        # run data — a bare DashboardState with no logs/live → empty.
        monkeypatch.chdir(tmp_path)
        client = TestClient(create_app(DashboardState()))
        resp = client.get("/api/strategy_positions")
        assert resp.status_code == 200
        assert resp.json()["strategies"] == []


class TestTradeHistoryCrossRun:
    def test_trade_history_sees_all_runs_via_log_dir(
        self, tmp_path: Path,
    ) -> None:
        log_dir = tmp_path / "live"
        # Run A: a round-trip (buy then sell) for alpha.
        _make_run(log_dir, "20260101T000000Z", [
            _buy("alpha", "BTCUSDT", "1", "100", "2026-01-01T00:00:01Z"),
            _wal_line("order_filled", "2026-01-01T01:00:00Z", {
                "strategy_id": "alpha", "symbol": "BTCUSDT", "side": "sell",
                "qty": "1", "price": "110", "fill_qty": "1", "fill_price": "110",
            }),
        ])
        # Run B: an open position for beta.
        _make_run(log_dir, "20260102T000000Z", [
            _buy("beta", "ETHUSDT", "2", "50", "2026-01-02T00:00:01Z"),
        ])
        state = DashboardState(log_dir=log_dir)
        client = TestClient(create_app(state))
        body = client.get("/api/trade_history").json()
        sids = {t["strategy_id"] for t in body["trades"]}
        assert "alpha" in sids and "beta" in sids, body


class TestResolveLogDirFallback:
    def test_falls_back_to_logs_live_when_dir_exists(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """No state.log_dir, no wal_path → fall back to ./logs/live if it exists."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs" / "live" / "20260101T000000Z").mkdir(parents=True)
        (tmp_path / "logs" / "live" / "20260101T000000Z" / "wal.jsonl").write_text(
            _buy("gamma", "BTCUSDT", "1", "100", "2026-01-01T00:00:01Z"),
            encoding="utf-8",
        )
        state = DashboardState()  # nothing set
        client = TestClient(create_app(state))
        body = client.get("/api/trade_history").json()
        assert body["log_dir_used"] is not None
        sids = {t["strategy_id"] for t in body["trades"]}
        assert "gamma" in sids, body

    def test_no_logs_live_dir_stays_none(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)  # no logs/live here
        state = DashboardState()
        client = TestClient(create_app(state))
        body = client.get("/api/trade_history").json()
        assert body["log_dir_used"] is None
        assert body["trades"] == []


# ── Issue 1: venue-inert visibility endpoint ───────────────────────────────

class _StubBuilder:
    def __init__(self, status):
        self.last_equity_status = status


class TestVenueEquityStatusEndpoint:
    def test_no_builder_wired_returns_unavailable(self) -> None:
        client = TestClient(create_app(DashboardState()))
        resp = client.get("/api/venue_equity_status")
        assert resp.status_code == 200
        assert resp.json() == {"available": False, "venues": {}}

    def test_surfaces_builder_status(self) -> None:
        state = DashboardState()
        state.snapshot_builder = _StubBuilder({
            "binance": {"ok": False, "reason": "creds", "equity": 0.0},
            "kis": {"ok": True, "reason": "", "equity": 1_000_000.0},
        })
        client = TestClient(create_app(state))
        body = client.get("/api/venue_equity_status").json()
        assert body["available"] is True
        assert body["venues"]["binance"]["ok"] is False
        assert body["venues"]["binance"]["reason"] == "creds"
        assert body["venues"]["kis"]["ok"] is True

    def test_robust_when_builder_attr_missing(self) -> None:
        """A builder object without last_equity_status must not 500."""
        state = DashboardState()
        state.snapshot_builder = object()
        client = TestClient(create_app(state))
        resp = client.get("/api/venue_equity_status")
        assert resp.status_code == 200
        assert resp.json()["venues"] == {}

    def test_dashboard_html_has_venue_indicator(self) -> None:
        client = TestClient(create_app(DashboardState()))
        body = client.get("/").text
        assert "venue_equity_status" in body or "venue-equity" in body
