"""수동 거래 입력 폼 + `/api/journal/today` 통합 endpoint (2026-05-21).

Claude Routines 일일 리포트 routine 의 데이터 source. 자동 fill (WAL) +
수동 거래 (`manual_trade.jsonl`) + cs-tsmom TOP-10 모두 한 endpoint 로 노출.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from src.dashboard.app import DashboardState, create_app


def _wal_line(event_type: str, ts: str, payload: dict) -> str:
    return json.dumps({
        "schema_version": 1, "ts": ts,
        "event_type": event_type, "payload": payload,
    }) + "\n"


def _mkdir_run(log_dir: Path, run_id: str, lines: list[str]) -> Path:
    rd = log_dir / run_id
    rd.mkdir(parents=True, exist_ok=True)
    wal = rd / "wal.jsonl"
    wal.write_text("".join(lines), encoding="utf-8")
    return wal


def _today_utc_iso(offset_h: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=offset_h)).isoformat()


# ── POST /api/manual_trade ─────────────────────────────────────────────────


class TestManualTradePost:
    def test_valid_post_appends_jsonl(self, tmp_path: Path) -> None:
        state = DashboardState(log_dir=tmp_path)
        client = TestClient(create_app(state))
        resp = client.post("/api/manual_trade", json={
            "symbol": "btcusdt", "side": "buy", "kind": "entry",
            "qty": 0.01, "price": 78000, "venue": "binance",
            "note": "RSI 28 + BB 하단",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        # File created + content valid JSONL
        path = Path(body["log_path"])
        assert path.exists()
        rec = json.loads(path.read_text(encoding="utf-8").strip())
        assert rec["event_type"] == "manual_trade"
        # 대문자 정규화 확인
        assert rec["payload"]["symbol"] == "BTCUSDT"
        assert rec["payload"]["note"] == "RSI 28 + BB 하단"

    def test_missing_symbol_400(self, tmp_path: Path) -> None:
        client = TestClient(create_app(DashboardState(log_dir=tmp_path)))
        resp = client.post("/api/manual_trade", json={
            "side": "buy", "qty": 1, "price": 100,
        })
        assert resp.status_code == 400

    def test_invalid_side_400(self, tmp_path: Path) -> None:
        client = TestClient(create_app(DashboardState(log_dir=tmp_path)))
        resp = client.post("/api/manual_trade", json={
            "symbol": "BTCUSDT", "side": "long", "qty": 1, "price": 100,
        })
        assert resp.status_code == 400

    def test_invalid_kind_400(self, tmp_path: Path) -> None:
        client = TestClient(create_app(DashboardState(log_dir=tmp_path)))
        resp = client.post("/api/manual_trade", json={
            "symbol": "BTCUSDT", "side": "buy", "kind": "hold",
            "qty": 1, "price": 100,
        })
        assert resp.status_code == 400

    def test_zero_qty_or_price_400(self, tmp_path: Path) -> None:
        client = TestClient(create_app(DashboardState(log_dir=tmp_path)))
        resp = client.post("/api/manual_trade", json={
            "symbol": "BTCUSDT", "side": "buy", "qty": 0, "price": 100,
        })
        assert resp.status_code == 400


# ── v2 schema (direction / entry_price / exit_price / realized_pnl / outcome)

class TestManualTradeV2Schema:
    def test_direction_long_with_exit_price_becomes_roundtrip(
        self, tmp_path: Path,
    ) -> None:
        client = TestClient(create_app(DashboardState(log_dir=tmp_path)))
        resp = client.post("/api/manual_trade", json={
            "symbol": "BTCUSDT",
            "direction": "long",
            "qty": 0.01,
            "entry_price": 77500,
            "exit_price": 77800,
            "realized_pnl": 3.0,
            "outcome": "win",
            "venue": "binance",
            "note": "BB lower bounce",
        })
        assert resp.status_code == 200, resp.text
        path = Path(resp.json()["log_path"])
        rec = json.loads(path.read_text(encoding="utf-8").strip())
        pl = rec["payload"]
        assert pl["direction"] == "long"
        assert pl["side"] == "buy"  # legacy 호환 자동 채움
        assert pl["kind"] == "roundtrip"  # exit_price 있으니 roundtrip
        assert pl["entry_price"] == 77500
        assert pl["exit_price"] == 77800
        assert pl["realized_pnl"] == 3.0
        assert pl["outcome"] == "win"

    def test_direction_short_only_entry_becomes_entry(
        self, tmp_path: Path,
    ) -> None:
        client = TestClient(create_app(DashboardState(log_dir=tmp_path)))
        resp = client.post("/api/manual_trade", json={
            "symbol": "AVAXUSDT",
            "direction": "short",
            "qty": 1,
            "entry_price": 9.5,
            "venue": "binance",
            "note": "airborne signal",
        })
        assert resp.status_code == 200
        path = Path(resp.json()["log_path"])
        rec = json.loads(path.read_text(encoding="utf-8").strip())
        pl = rec["payload"]
        assert pl["direction"] == "short"
        assert pl["side"] == "sell"
        assert pl["kind"] == "entry"
        assert pl["exit_price"] is None
        assert pl["realized_pnl"] is None
        assert pl["outcome"] is None

    def test_legacy_side_buy_back_compat(self, tmp_path: Path) -> None:
        """v1 payload (side='buy', price=N) 가 그대로 동작 + direction 자동 채움."""
        client = TestClient(create_app(DashboardState(log_dir=tmp_path)))
        resp = client.post("/api/manual_trade", json={
            "symbol": "BTCUSDT", "side": "buy", "kind": "entry",
            "qty": 0.01, "price": 78000, "venue": "binance",
        })
        assert resp.status_code == 200
        rec = json.loads(
            Path(resp.json()["log_path"]).read_text(encoding="utf-8").strip()
        )
        pl = rec["payload"]
        assert pl["side"] == "buy"
        assert pl["direction"] == "long"  # auto-derive
        assert pl["entry_price"] == 78000
        assert pl["price"] == 78000  # legacy 호환 유지

    def test_bad_outcome_400(self, tmp_path: Path) -> None:
        client = TestClient(create_app(DashboardState(log_dir=tmp_path)))
        resp = client.post("/api/manual_trade", json={
            "symbol": "BTCUSDT", "direction": "long", "qty": 0.01,
            "entry_price": 78000, "outcome": "win-ish",
        })
        assert resp.status_code == 400

    def test_recent_endpoint_returns_all_time(self, tmp_path: Path) -> None:
        client = TestClient(create_app(DashboardState(log_dir=tmp_path)))
        client.post("/api/manual_trade", json={
            "symbol": "BTCUSDT", "direction": "long",
            "qty": 0.01, "entry_price": 78000,
        })
        resp = client.get("/api/manual_trade/recent?limit=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_all_time"] == 1
        assert len(body["trades"]) == 1


# ── GET /api/manual_trade/today ────────────────────────────────────────────


class TestManualTradeToday:
    def test_empty_when_no_file(self, tmp_path: Path) -> None:
        client = TestClient(create_app(DashboardState(log_dir=tmp_path)))
        resp = client.get("/api/manual_trade/today")
        assert resp.status_code == 200
        assert resp.json()["trades"] == []

    def test_returns_today_entries(self, tmp_path: Path) -> None:
        state = DashboardState(log_dir=tmp_path)
        client = TestClient(create_app(state))
        # Post 2 trades
        for sym in ("BTCUSDT", "ETHUSDT"):
            client.post("/api/manual_trade", json={
                "symbol": sym, "side": "buy", "kind": "entry",
                "qty": 0.1, "price": 100, "venue": "binance",
                "note": f"test {sym}",
            })
        resp = client.get("/api/manual_trade/today")
        body = resp.json()
        assert len(body["trades"]) == 2
        syms = {t["symbol"] for t in body["trades"]}
        assert syms == {"BTCUSDT", "ETHUSDT"}


# ── GET /api/journal/today (통합) ──────────────────────────────────────────


class TestJournalTodayEndpoint:
    def test_empty_when_no_data(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        client = TestClient(create_app(DashboardState()))
        resp = client.get("/api/journal/today")
        assert resp.status_code == 200
        body = resp.json()
        assert body["counts"]["auto_fills"] == 0
        assert body["counts"]["auto_signals"] == 0
        assert body["counts"]["manual_trades"] == 0
        assert body["auto_fills"] == []
        assert body["manual_trades"] == []

    def test_aggregates_auto_wal_and_manual_jsonl(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "live"
        log_dir.mkdir()
        # 오늘 자동 fill 1 + signal 1 (WAL)
        ts_now = _today_utc_iso()
        _mkdir_run(log_dir, "run-001", [
            _wal_line("fill_received", ts_now, {
                "strategy_id": "cs_tsmom_crypto_daily", "symbol": "BTCUSDT",
                "side": "buy", "qty": "0.01", "price": "78000",
            }),
            _wal_line("signal_emitted", ts_now, {
                "strategy_id": "cs_tsmom_crypto_daily", "symbol": "BTCUSDT",
                "side": "buy", "qty": "0.01",
                "reason": "cs_basket_dispatch:cs_tsmom_crypto_daily",
            }),
        ])
        state = DashboardState(log_dir=log_dir)
        client = TestClient(create_app(state))
        # 수동 거래도 1건 POST
        client.post("/api/manual_trade", json={
            "symbol": "ETHUSDT", "side": "buy", "kind": "entry",
            "qty": 0.5, "price": 2100, "venue": "binance",
            "note": "MACD 골든크로스",
        })
        resp = client.get("/api/journal/today")
        body = resp.json()
        assert body["counts"]["auto_fills"] == 1
        assert body["counts"]["auto_signals"] == 1
        assert body["counts"]["manual_trades"] == 1
        assert body["auto_fills"][0]["symbol"] == "BTCUSDT"
        assert body["auto_signals"][0]["reason"].startswith("cs_basket_dispatch")
        assert body["manual_trades"][0]["symbol"] == "ETHUSDT"
        assert body["manual_trades"][0]["note"] == "MACD 골든크로스"

    def test_old_events_excluded(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "live"
        log_dir.mkdir()
        # 이틀 전 fill — KST 자정 cutoff 보다 이전이므로 제외
        ts_old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        _mkdir_run(log_dir, "run-old", [
            _wal_line("fill_received", ts_old, {
                "strategy_id": "old", "symbol": "BTCUSDT",
                "side": "buy", "qty": "1", "price": "70000",
            }),
        ])
        state = DashboardState(log_dir=log_dir)
        client = TestClient(create_app(state))
        body = client.get("/api/journal/today").json()
        assert body["counts"]["auto_fills"] == 0


# ── /manual HTML page ─────────────────────────────────────────────────────


class TestManualPageHtml:
    def test_renders(self) -> None:
        client = TestClient(create_app(DashboardState()))
        resp = client.get("/manual")
        assert resp.status_code == 200
        body = resp.text
        assert "수동 거래 입력" in body
        assert "/api/manual_trade" in body
        assert "submitTrade" in body

    def test_dashboard_nav_links_manual(self) -> None:
        client = TestClient(create_app(DashboardState()))
        body = client.get("/").text
        assert 'href="/manual"' in body
