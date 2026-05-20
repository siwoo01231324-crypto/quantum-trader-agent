"""Signal-list page (#268) — `/api/signals` + `/signals` page.

Verifies:
  - Binance venue filter excludes KRX 6-digit symbols.
  - Follow-up resolution: filled / ordered / pending.
  - Read-only graceful behavior when WAL/log-dir absent.
  - HTML page renders with the expected scaffolding.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.dashboard.app import DashboardState, create_app


def _wal_line(event_type: str, ts: str, payload: dict) -> str:
    return json.dumps({
        "schema_version": 1,
        "ts": ts,
        "event_type": event_type,
        "payload": payload,
    }) + "\n"


def _signal(strategy_id: str, symbol: str, side: str, qty: str, reason: str, ts: str) -> str:
    return _wal_line("signal_emitted", ts, {
        "strategy_id": strategy_id, "symbol": symbol, "side": side,
        "qty": qty, "reason": reason,
    })


def _order_acked(strategy_id: str, symbol: str, side: str, ts: str) -> str:
    return _wal_line("order_acked", ts, {
        "strategy_id": strategy_id, "symbol": symbol, "side": side,
        "status": "NEW",
    })


def _fill(strategy_id: str, symbol: str, side: str, qty: str, price: str, ts: str) -> str:
    return _wal_line("fill_received", ts, {
        "strategy_id": strategy_id, "symbol": symbol, "side": side,
        "qty": qty, "price": price,
    })


def _make_run(log_dir: Path, run_id: str, lines: list[str]) -> Path:
    rd = log_dir / run_id
    rd.mkdir(parents=True, exist_ok=True)
    wal = rd / "wal.jsonl"
    wal.write_text("".join(lines), encoding="utf-8")
    return wal


class TestApiSignalsVenueFilter:
    def test_binance_venue_excludes_krx_symbols(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "live"
        _make_run(log_dir, "20260101T000000Z", [
            _signal("live-rsi", "BTCUSDT", "buy", "0.01", "rsi<30",
                    "2026-01-01T00:00:00Z"),
            _signal("kis-mom", "005930", "buy", "10", "mom",
                    "2026-01-01T00:00:10Z"),
        ])
        client = TestClient(create_app(DashboardState(log_dir=log_dir)))
        body = client.get("/api/signals?venue=binance").json()
        syms = {s["symbol"] for s in body["signals"]}
        assert syms == {"BTCUSDT"}, body
        assert body["venue"] == "binance"

    def test_venue_all_includes_everything(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "live"
        _make_run(log_dir, "20260101T000000Z", [
            _signal("live-rsi", "BTCUSDT", "buy", "0.01", "rsi<30",
                    "2026-01-01T00:00:00Z"),
            _signal("kis-mom", "005930", "buy", "10", "mom",
                    "2026-01-01T00:00:10Z"),
        ])
        client = TestClient(create_app(DashboardState(log_dir=log_dir)))
        body = client.get("/api/signals?venue=all").json()
        syms = {s["symbol"] for s in body["signals"]}
        assert syms == {"BTCUSDT", "005930"}, body


class TestApiSignalsFollowUp:
    def test_filled_when_fill_within_window(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "live"
        _make_run(log_dir, "20260101T000000Z", [
            _signal("live-rsi", "BTCUSDT", "buy", "0.01", "rsi<30",
                    "2026-01-01T00:00:00Z"),
            _fill("live-rsi", "BTCUSDT", "buy", "0.01", "30000",
                  "2026-01-01T00:00:30Z"),
        ])
        client = TestClient(create_app(DashboardState(log_dir=log_dir)))
        body = client.get("/api/signals?venue=binance").json()
        assert body["signals"][0]["follow_up"] == "filled", body

    def test_ordered_when_only_ack_present(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "live"
        _make_run(log_dir, "20260101T000000Z", [
            _signal("live-rsi", "ETHUSDT", "buy", "0.5", "rsi<30",
                    "2026-01-01T00:00:00Z"),
            _order_acked("live-rsi", "ETHUSDT", "buy",
                         "2026-01-01T00:00:20Z"),
        ])
        client = TestClient(create_app(DashboardState(log_dir=log_dir)))
        body = client.get("/api/signals?venue=binance").json()
        assert body["signals"][0]["follow_up"] == "ordered", body

    def test_pending_when_nothing_follows(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "live"
        _make_run(log_dir, "20260101T000000Z", [
            _signal("live-rsi", "BTCUSDT", "buy", "0.01", "blocked-by-meta",
                    "2026-01-01T00:00:00Z"),
        ])
        client = TestClient(create_app(DashboardState(log_dir=log_dir)))
        body = client.get("/api/signals?venue=binance").json()
        assert body["signals"][0]["follow_up"] == "pending", body

    def test_pending_when_match_outside_window(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "live"
        _make_run(log_dir, "20260101T000000Z", [
            _signal("live-rsi", "BTCUSDT", "buy", "0.01", "rsi<30",
                    "2026-01-01T00:00:00Z"),
            # Fill 3 minutes later — outside 120s window.
            _fill("live-rsi", "BTCUSDT", "buy", "0.01", "30000",
                  "2026-01-01T00:03:00Z"),
        ])
        client = TestClient(create_app(DashboardState(log_dir=log_dir)))
        body = client.get("/api/signals?venue=binance").json()
        assert body["signals"][0]["follow_up"] == "pending", body


class TestApiSignalsOrdering:
    def test_newest_first(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "live"
        _make_run(log_dir, "20260101T000000Z", [
            _signal("s1", "BTCUSDT", "buy", "0.01", "r1",
                    "2026-01-01T00:00:00Z"),
            _signal("s2", "ETHUSDT", "sell", "0.5", "r2",
                    "2026-01-01T01:00:00Z"),
        ])
        client = TestClient(create_app(DashboardState(log_dir=log_dir)))
        body = client.get("/api/signals?venue=binance").json()
        assert [s["strategy_id"] for s in body["signals"]] == ["s2", "s1"]


class TestApiSignalsRobustness:
    def test_missing_log_dir_returns_empty_never_500(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)  # no logs/live here
        client = TestClient(create_app(DashboardState()))
        resp = client.get("/api/signals?venue=binance")
        assert resp.status_code == 200
        body = resp.json()
        assert body["signals"] == []
        assert body["log_dir_used"] is None

    def test_aggregates_across_runs(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "live"
        _make_run(log_dir, "20260101T000000Z", [
            _signal("s1", "BTCUSDT", "buy", "0.01", "r1",
                    "2026-01-01T00:00:00Z"),
        ])
        _make_run(log_dir, "20260102T000000Z", [
            _signal("s2", "ETHUSDT", "buy", "0.5", "r2",
                    "2026-01-02T00:00:00Z"),
        ])
        client = TestClient(create_app(DashboardState(log_dir=log_dir)))
        sids = {
            s["strategy_id"]
            for s in client.get("/api/signals?venue=binance").json()["signals"]
        }
        assert sids == {"s1", "s2"}

    def test_limit_truncates(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "live"
        lines = [
            _signal(f"s{i}", "BTCUSDT", "buy", "0.01", "r",
                    f"2026-01-01T00:{i:02d}:00Z")
            for i in range(10)
        ]
        _make_run(log_dir, "20260101T000000Z", lines)
        client = TestClient(create_app(DashboardState(log_dir=log_dir)))
        body = client.get("/api/signals?venue=binance&limit=3").json()
        assert len(body["signals"]) == 3
        assert body["total"] == 10
        assert body["truncated"] is True


class TestSignalsPageHtml:
    def test_page_renders(self) -> None:
        client = TestClient(create_app(DashboardState()))
        resp = client.get("/signals")
        assert resp.status_code == 200
        body = resp.text
        assert "신호 목록" in body
        assert "/api/signals" in body  # JS pulls from this endpoint
        assert "BTCUSDT" not in body  # rows are fetched client-side

    def test_dashboard_nav_links_to_signals(self) -> None:
        client = TestClient(create_app(DashboardState()))
        body = client.get("/").text
        assert 'href="/signals"' in body
