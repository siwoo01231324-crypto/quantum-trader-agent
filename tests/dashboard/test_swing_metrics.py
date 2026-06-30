"""Tests for the /swing dashboard page (스윙 2전략 신호 수익률 누적 분석).

ma-cross/airborne 대시보드 테스트 미러. /swing 은 라이브 데몬이 아닌 과거 4h
봉에 두 스윙 전략 객체를 직접 구동한 합성 거래(sim==live)를 집계한다. 본
테스트는 무거운 실거동(parquet 전 유니버스 구동)을 피하려고 ``compute`` 함수를
합성 거래로 monkeypatch 하고, 순수 집계 함수 + SwingSimCache 영속성 + 라우트/
엔드포인트 스모크를 검증한다.

가드:
1. SwingSimCache — put/load/dedup/clear (logs/swing/sim_cache.jsonl 패턴).
2. _aggregate_swing_trades — n/win/pf/mean/sum/net/by_reason/by_symbol shape.
3. _swing_per_year — 연도별 cap/don/combined.
4. _swing_run_on_bar — 동기 코루틴 구동이 Signal 반환.
5. /swing 페이지 HTML + /api/swing_metrics JSON 구조 (compute monkeypatch).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import src.dashboard.app as app_mod
from src.dashboard.app import (
    _aggregate_swing_trades,
    _swing_per_year,
    _swing_run_on_bar,
    app,
)
from src.dashboard.swing_sim_cache import SwingSimCache

_C = TestClient(app)


def _trade(strategy="live-capitulation-bounce", symbol="ATOMUSDT",
           entry_ts="2024-01-01T00:00:00+00:00", exit_ts="2024-01-02T00:00:00+00:00",
           ret=2.0, reason="tp"):
    return {
        "strategy": strategy, "symbol": symbol,
        "entry_ts": entry_ts, "exit_ts": exit_ts,
        "entry": 100.0, "exit": 100.0 * (1 + ret / 100), "ret": ret, "reason": reason,
    }


# ── SwingSimCache ───────────────────────────────────────────────────────────

class TestSwingSimCache:
    @pytest.fixture
    def cache(self, tmp_path):
        return SwingSimCache(tmp_path / "sim_cache.jsonl")

    def test_initial_empty(self, cache):
        assert cache.is_empty()
        assert cache.load_all() == []
        assert cache.count() == 0

    def test_put_and_load(self, cache):
        added = cache.put_many([_trade(symbol="ATOMUSDT"), _trade(symbol="DOTUSDT")])
        assert added == 2
        assert not cache.is_empty()
        loaded = cache.load_all()
        assert {t["symbol"] for t in loaded} == {"ATOMUSDT", "DOTUSDT"}

    def test_dedup_on_strategy_symbol_entry(self, cache):
        a1 = cache.put_many([_trade(symbol="ATOMUSDT")])
        a2 = cache.put_many([_trade(symbol="ATOMUSDT")])  # 같은 key → dedup
        # 다른 전략이면 같은 종목·시각이라도 별개
        a3 = cache.put_many([_trade(strategy="live-donchian-breakout-btcgate",
                                    symbol="ATOMUSDT")])
        assert (a1, a2, a3) == (1, 0, 1)
        assert cache.count() == 2

    def test_skips_malformed(self, cache):
        added = cache.put_many([
            {"strategy": "", "symbol": "X", "entry_ts": "t"},
            {"strategy": "s", "symbol": "", "entry_ts": "t"},
            {"strategy": "s", "symbol": "X", "entry_ts": ""},
        ])
        assert added == 0

    def test_clear(self, cache):
        cache.put_many([_trade()])
        assert cache.count() == 1
        cache.clear()
        assert cache.is_empty()
        assert cache.count() == 0

    def test_reopen_preserves(self, tmp_path):
        c1 = SwingSimCache(tmp_path / "s.jsonl")
        c1.put_many([_trade(symbol="ATOMUSDT"), _trade(symbol="DOTUSDT")])
        c2 = SwingSimCache(tmp_path / "s.jsonl")
        assert c2.count() == 2
        assert c2.put_many([_trade(symbol="ATOMUSDT")]) == 0  # dedup after reopen

    def test_corrupted_line_skipped(self, tmp_path):
        path = tmp_path / "s.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            f.write('{"strategy":"s","symbol":"A","entry_ts":"t1","ret":1.0}\n')
            f.write("garbage line\n")
            f.write('{"strategy":"s","symbol":"B","entry_ts":"t2","ret":-1.0}\n')
        c = SwingSimCache(path)
        assert c.count() == 2


# ── _aggregate_swing_trades ─────────────────────────────────────────────────

class TestAggregate:
    def test_empty(self):
        agg = _aggregate_swing_trades([])
        assert agg["n"] == 0
        assert agg["pf"] is None
        assert agg["by_reason"] == {}
        assert agg["by_symbol"] == []

    def test_basic_metrics(self):
        trades = [
            _trade(symbol="A", ret=3.0, reason="tp"),
            _trade(symbol="A", ret=-1.0, reason="stop"),
            _trade(symbol="B", ret=2.0, reason="channel_exit"),
        ]
        agg = _aggregate_swing_trades(trades)
        assert agg["n"] == 3
        assert agg["win_rate"] == pytest.approx(2 / 3)
        assert agg["sum_pct"] == pytest.approx(4.0)
        # net = gross - fee_pct * n  (fee 0.10 × 3)
        assert agg["net_pct"] == pytest.approx(4.0 - 0.10 * 3)
        # PF = (3+2) / |−1| = 5
        assert agg["pf"] == pytest.approx(5.0)
        assert agg["mean_pct"] == pytest.approx(4.0 / 3)

    def test_by_reason_and_symbol(self):
        trades = [
            _trade(symbol="A", ret=3.0, reason="tp"),
            _trade(symbol="A", ret=1.0, reason="tp"),
            _trade(symbol="B", ret=-2.0, reason="stop"),
        ]
        agg = _aggregate_swing_trades(trades)
        assert agg["by_reason"]["tp"]["n"] == 2
        assert agg["by_reason"]["tp"]["sum_pct"] == pytest.approx(4.0)
        assert agg["by_reason"]["stop"]["n"] == 1
        # by_symbol sorted by sum_pct desc → A(+4) first, B(-2) last
        assert agg["by_symbol"][0]["symbol"] == "A"
        assert agg["by_symbol"][-1]["symbol"] == "B"

    def test_no_losses_pf_none(self):
        agg = _aggregate_swing_trades([_trade(ret=1.0), _trade(symbol="B", ret=2.0)])
        assert agg["pf"] is None  # 손실 없음 → PF undefined


# ── _swing_per_year ─────────────────────────────────────────────────────────

class TestPerYear:
    def test_per_year_pair(self):
        cap = [
            _trade(entry_ts="2025-03-01T00:00:00+00:00", ret=5.0),
            _trade(entry_ts="2026-03-01T00:00:00+00:00", ret=4.0),
        ]
        don = [
            _trade(strategy="live-donchian-breakout-btcgate",
                   entry_ts="2025-06-01T00:00:00+00:00", ret=10.0),
            _trade(strategy="live-donchian-breakout-btcgate",
                   entry_ts="2026-06-01T00:00:00+00:00", ret=-3.0),
        ]
        rows = _swing_per_year(cap, don)
        years = [r["year"] for r in rows]
        assert years == ["2025", "2026"]
        r2026 = next(r for r in rows if r["year"] == "2026")
        assert r2026["cap"]["sum_pct"] == pytest.approx(4.0)
        assert r2026["don"]["sum_pct"] == pytest.approx(-3.0)
        assert r2026["combined"]["sum_pct"] == pytest.approx(1.0)
        assert r2026["combined"]["n"] == 2

    def test_per_year_empty(self):
        assert _swing_per_year([], []) == []


# ── _swing_run_on_bar (동기 코루틴 구동) ─────────────────────────────────────

def test_run_on_bar_returns_signal():
    from src.backtest.strategies.live_capitulation_bounce import LiveCapitulationBounce

    s = LiveCapitulationBounce()
    idx = pd.date_range("2024-01-01", periods=40, freq="4h")
    flat = np.ones(40) * 100.0
    df = pd.DataFrame(
        {"open": flat, "high": flat * 1.01, "low": flat * 0.99,
         "close": flat, "volume": np.ones(40) * 10.0},
        index=idx,
    )
    ctx = {"market_snapshot": {"history": df, "universe_ohlcv": {"BTCUSDT": df}}}
    sig = _swing_run_on_bar(s, ctx)
    assert sig is not None
    assert sig.action in ("hold", "buy")  # flat 시장 → 진입 안 됨(hold)


# ── route + endpoint smoke (compute monkeypatched) ──────────────────────────

@pytest.fixture
def patched_swing(tmp_path, monkeypatch):
    """무거운 실구동 대신 합성 거래로 compute 를 대체 + 임시 sim 캐시."""
    fake_trades = [
        _trade(strategy="live-capitulation-bounce", symbol="ATOMUSDT",
               entry_ts="2025-01-01T00:00:00+00:00", ret=3.0, reason="tp"),
        _trade(strategy="live-capitulation-bounce", symbol="DOTUSDT",
               entry_ts="2026-01-01T00:00:00+00:00", ret=-1.0, reason="stop"),
        _trade(strategy="live-donchian-breakout-btcgate", symbol="ATOMUSDT",
               entry_ts="2025-02-01T00:00:00+00:00", ret=8.0, reason="channel_exit"),
        _trade(strategy="live-donchian-breakout-btcgate", symbol="ETCUSDT",
               entry_ts="2026-02-01T00:00:00+00:00", ret=-2.0, reason="stop"),
    ]
    monkeypatch.setattr(app_mod, "_swing_compute_all_trades", lambda: fake_trades)
    monkeypatch.setattr(app_mod, "_SWING_SIM_CACHE",
                        SwingSimCache(tmp_path / "sim_cache.jsonl"))
    return fake_trades


def test_swing_page_html():
    h = _C.get("/swing")
    assert h.status_code == 200
    body = h.text
    assert "스윙 2전략" in body
    assert "/api/swing_metrics" in body
    assert "function render" in body


def test_swing_metrics_structure(patched_swing):
    j = _C.get("/api/swing_metrics?refresh=1").json()
    assert j["trade_count"] == 4
    assert j["universe_size"] >= 1
    # combined
    assert j["combined"]["n"] == 4
    assert j["combined"]["sum_pct"] == pytest.approx(3 - 1 + 8 - 2)
    # per-strategy blocks
    cap = j["strategies"]["live-capitulation-bounce"]
    don = j["strategies"]["live-donchian-breakout-btcgate"]
    assert cap["n"] == 2 and don["n"] == 2
    assert cap["label"]
    # per-year diversification (2025/2026)
    years = [r["year"] for r in j["per_year"]]
    assert years == ["2025", "2026"]
    # exit-reason breakdown present
    assert "by_reason" in cap
    assert "by_symbol" in j["combined"]


def test_swing_metrics_cached_flag(patched_swing):
    # 첫 호출 refresh → 재구동, 두번째 → 캐시
    _C.get("/api/swing_metrics?refresh=1")
    j2 = _C.get("/api/swing_metrics").json()
    assert j2["cached"] is True


def test_swing_metrics_empty_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "_swing_compute_all_trades", lambda: [])
    monkeypatch.setattr(app_mod, "_SWING_SIM_CACHE",
                        SwingSimCache(tmp_path / "empty.jsonl"))
    j = _C.get("/api/swing_metrics?refresh=1").json()
    assert j["trade_count"] == 0
    assert j["combined"]["n"] == 0
    assert j["per_year"] == []


# ── swing_live (라이브 testnet/demo WAL 윈도우 집계) ─────────────────────────
import json as _json  # noqa: E402
from datetime import datetime as _dt, timezone as _tz  # noqa: E402

from src.dashboard.swing_live import (  # noqa: E402
    aggregate_swing_live,
    aggregate_swing_window,
    discover_swing_wal_files,
    exit_reason_label,
    window_sim_trades,
)


def _wal_line(event_type, ts, **payload):
    return _json.dumps(
        {"ts": ts, "event_type": event_type, "schema_version": 1, "payload": payload}
    )


def _write_swing_wal(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _swing_live_fixture(tmp_path):
    """1 청산(tp 라운드트립) + 1 보유(미청산) + 진입신호 2 — 합성 WAL."""
    wal = tmp_path / "logs" / "shadow-swing" / "run1" / "wal.jsonl"
    lines = [
        _wal_line("run_started", "2026-06-29T00:00:00+00:00", run_id="run1"),
        # 투매반등: buy 진입 → sell tp 청산 (+10%)
        _wal_line("signal_emitted", "2026-06-29T01:00:00+00:00",
                  strategy_id="live-capitulation-bounce", symbol="ATOMUSDT",
                  side="buy", reason="capitulation_bounce"),
        _wal_line("order_filled", "2026-06-29T01:00:01+00:00",
                  strategy_id="live-capitulation-bounce", symbol="ATOMUSDT",
                  side="BUY", fill_qty="1", fill_price="100", fees="0"),
        _wal_line("signal_emitted", "2026-06-29T05:00:00+00:00",
                  strategy_id="live-capitulation-bounce", symbol="ATOMUSDT",
                  side="sell", reason="live_take_profit"),
        _wal_line("order_filled", "2026-06-29T05:00:01+00:00",
                  strategy_id="live-capitulation-bounce", symbol="ATOMUSDT",
                  side="SELL", fill_qty="1", fill_price="110", fees="0"),
        # 돌파: buy 진입만 → 미청산(보유중)
        _wal_line("signal_emitted", "2026-06-29T02:00:00+00:00",
                  strategy_id="live-donchian-breakout-btcgate", symbol="DOTUSDT",
                  side="buy", reason="donchian_breakout"),
        _wal_line("order_filled", "2026-06-29T02:00:01+00:00",
                  strategy_id="live-donchian-breakout-btcgate", symbol="DOTUSDT",
                  side="BUY", fill_qty="2", fill_price="50", fees="0"),
    ]
    _write_swing_wal(wal, lines)
    return wal


_WIN_SINCE = _dt(2026, 6, 29, 0, 0, tzinfo=_tz.utc)
_WIN_UNTIL = _dt(2026, 6, 30, 0, 0, tzinfo=_tz.utc)


class TestSwingLiveAggregate:
    def test_empty_no_paths(self):
        agg = aggregate_swing_live([], _WIN_SINCE, _WIN_UNTIL)
        assert agg["n_signals"] == 0
        assert agg["n_trades_closed"] == 0
        assert agg["open_positions"] == 0
        assert agg["net_pnl"] == 0.0
        assert agg["trades"] == []
        assert agg["signals"] == []

    def test_roundtrip_and_open(self, tmp_path):
        wal = _swing_live_fixture(tmp_path)
        agg = aggregate_swing_live([wal], _WIN_SINCE, _WIN_UNTIL)
        # 진입 신호 2건 (투매 buy + 돌파 buy)
        assert agg["n_signals"] == 2
        # 청산 라운드트립 1건 (ATOM tp), 승 1 / 패 0
        assert agg["n_trades_closed"] == 1
        assert agg["wins"] == 1
        assert agg["losses"] == 0
        # 실현 NET = (110-100)*1 - 0 = +10
        assert agg["net_pnl"] == pytest.approx(10.0)
        # 보유중 1건 (DOT 미청산)
        assert agg["open_positions"] == 1
        # 표 2행 (청산 1 + 보유 1)
        assert len(agg["trades"]) == 2
        closed = next(t for t in agg["trades"] if t["status"] == "closed")
        assert closed["symbol"] == "ATOMUSDT"
        assert closed["pct"] == pytest.approx(10.0)
        assert closed["side"] == "long"
        # 청산 사유 매칭 (live_take_profit → 익절 라벨)
        assert "익절" in closed["status_label"]
        opened = next(t for t in agg["trades"] if t["status"] == "open")
        assert opened["symbol"] == "DOTUSDT"
        assert opened["status_label"] == "보유중"
        assert opened["pct"] is None

    def test_window_excludes_out_of_range(self, tmp_path):
        wal = _swing_live_fixture(tmp_path)
        # 어제(2026-06-28) 윈도우 → 거래 없음
        prev_since = _dt(2026, 6, 28, 0, 0, tzinfo=_tz.utc)
        prev_until = _dt(2026, 6, 29, 0, 0, tzinfo=_tz.utc)
        agg = aggregate_swing_live([wal], prev_since, prev_until)
        assert agg["n_signals"] == 0
        assert agg["n_trades_closed"] == 0
        # 보유중도 entry_ts(06-29) >= until(06-29 00:00) 이라 제외
        assert agg["open_positions"] == 0
        assert agg["trades"] == []

    def test_filters_foreign_strategy(self, tmp_path):
        wal = tmp_path / "logs" / "shadow-swing" / "runX" / "wal.jsonl"
        _write_swing_wal(wal, [
            _wal_line("order_filled", "2026-06-29T03:00:00+00:00",
                      strategy_id="some-other-strategy", symbol="ETHUSDT",
                      side="BUY", fill_qty="1", fill_price="2000", fees="0"),
            _wal_line("signal_emitted", "2026-06-29T03:00:00+00:00",
                      strategy_id="some-other-strategy", symbol="ETHUSDT",
                      side="buy", reason="x"),
        ])
        agg = aggregate_swing_live([wal], _WIN_SINCE, _WIN_UNTIL)
        assert agg["n_signals"] == 0
        assert agg["open_positions"] == 0
        assert agg["trades"] == []


class TestSwingLiveDiscover:
    def test_scans_both_dirs(self, tmp_path):
        _write_swing_wal(tmp_path / "logs" / "shadow-swing" / "r1" / "wal.jsonl",
                         [_wal_line("run_started", "2026-06-29T00:00:00+00:00")])
        _write_swing_wal(tmp_path / "logs" / "shadow-swing-binance" / "r2" / "wal.jsonl",
                         [_wal_line("run_started", "2026-06-29T00:00:00+00:00")])
        found = discover_swing_wal_files(tmp_path)
        assert len(found) == 2
        names = {p.parent.name for p in found}
        assert names == {"r1", "r2"}

    def test_missing_dirs_empty(self, tmp_path):
        assert discover_swing_wal_files(tmp_path) == []


class TestExitReasonLabel:
    def test_labels(self):
        assert "익절" in exit_reason_label("live_take_profit")
        assert "손절" in exit_reason_label("live_stop_loss")
        assert "트레일" in exit_reason_label("live_trailing_stop")
        assert "채널" in exit_reason_label("channel_exit")
        assert exit_reason_label(None) == "청산"


# ── sim(백테스트) 윈도우 병합 ────────────────────────────────────────────────

def _sim_trade(strategy="live-capitulation-bounce", symbol="ATOMUSDT",
               entry_ts="2026-06-29T03:00:00+00:00",
               exit_ts="2026-06-29T07:00:00+00:00", ret=3.0, reason="tp"):
    return {
        "strategy": strategy, "symbol": symbol,
        "entry_ts": entry_ts, "exit_ts": exit_ts,
        "entry": 100.0, "exit": 100.0 * (1 + ret / 100), "ret": ret, "reason": reason,
    }


class TestWindowSimTrades:
    def test_filters_by_entry_ts(self):
        sim = [
            _sim_trade(entry_ts="2026-06-29T03:00:00+00:00", ret=3.0),          # in
            _sim_trade(symbol="DOTUSDT",
                       entry_ts="2025-01-01T00:00:00+00:00", ret=5.0),          # out
        ]
        rows = window_sim_trades(sim, _WIN_SINCE, _WIN_UNTIL)
        assert len(rows) == 1
        assert rows[0]["source"] == "sim"
        assert rows[0]["symbol"] == "ATOMUSDT"
        assert rows[0]["side"] == "long"
        assert rows[0]["status"] == "closed"
        assert rows[0]["pct"] == pytest.approx(3.0)
        assert rows[0]["pnl"] is None  # sim 은 % 기반, USDT 손익 없음

    def test_filters_foreign_strategy(self):
        sim = [_sim_trade(strategy="other-strat",
                          entry_ts="2026-06-29T03:00:00+00:00")]
        assert window_sim_trades(sim, _WIN_SINCE, _WIN_UNTIL) == []


class TestAggregateSwingWindow:
    def test_merges_sim_and_live(self, tmp_path):
        wal = _swing_live_fixture(tmp_path)  # 라이브: 2026-06-29 (청산1+보유1, 신호2)
        sim = [
            _sim_trade(symbol="FILUSDT",
                       entry_ts="2026-06-29T03:00:00+00:00", ret=4.0, reason="tp"),
            _sim_trade(symbol="ETCUSDT",
                       entry_ts="2026-06-29T06:00:00+00:00", ret=-2.0, reason="stop"),
        ]
        agg = aggregate_swing_window([wal], sim, _WIN_SINCE, _WIN_UNTIL)
        # sim 블록 (gross/net%)
        assert agg["sim"]["n"] == 2
        assert agg["sim"]["wins"] == 1 and agg["sim"]["losses"] == 1
        assert agg["sim"]["sum_pct"] == pytest.approx(2.0)        # +4 −2
        assert agg["sim"]["net_pct"] == pytest.approx(2.0 - 0.10 * 2)
        # live 블록 보존
        assert agg["live"]["n_signals"] == 2
        assert agg["live"]["n_trades_closed"] == 1
        assert agg["live"]["open_positions"] == 1
        # 병합 trades = live(청산1+보유1) + sim 2 = 4, source 태그 존재
        assert len(agg["trades"]) == 4
        assert {t["source"] for t in agg["trades"]} == {"sim", "live"}
        assert sum(1 for t in agg["trades"] if t["source"] == "sim") == 2
        assert agg["has_data"] is True
        # 하위호환: top-level live 키 유지
        assert agg["n_signals"] == 2
        assert agg["n_trades_closed"] == 1

    def test_sim_only_not_empty(self):
        # 라이브 WAL 전무 + sim 만 있어도 윈도우는 비지 않는다 (핵심 요구사항)
        sim = [_sim_trade(entry_ts="2026-06-29T03:00:00+00:00", ret=1.5)]
        agg = aggregate_swing_window([], sim, _WIN_SINCE, _WIN_UNTIL)
        assert agg["sim"]["n"] == 1
        assert agg["has_data"] is True
        assert len(agg["trades"]) == 1
        assert agg["trades"][0]["source"] == "sim"

    def test_both_empty(self):
        agg = aggregate_swing_window([], [], _WIN_SINCE, _WIN_UNTIL)
        assert agg["has_data"] is False
        assert agg["trades"] == []
        assert agg["sim"]["n"] == 0
        assert agg["live"]["n_signals"] == 0


class TestSwingLiveEndpoint:
    @staticmethod
    def _patch_sim(monkeypatch, tmp_path, sim_trades=None):
        """엔드포인트가 무거운 실 sim 구동을 안 하도록 임시 캐시 주입."""
        cache = SwingSimCache(tmp_path / "sim_cache.jsonl")
        if sim_trades:
            cache.put_many(sim_trades)
        monkeypatch.setattr(app_mod, "_SWING_SIM_CACHE", cache)
        monkeypatch.setattr(app_mod, "_swing_compute_all_trades", lambda: [])

    def test_endpoint_window_all_smoke(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app_mod, "_swing_repo_root", lambda: tmp_path)
        self._patch_sim(monkeypatch, tmp_path)
        _swing_live_fixture(tmp_path)
        j = _C.get("/api/swing_live?window=all&refresh=1").json()
        assert "error" not in j
        assert j["window"] == "all"
        assert isinstance(j["trades"], list)
        assert j["wal_files_count"] >= 1
        # sim 블록 존재 (빈 캐시 → n=0)
        assert j["sim"]["n"] == 0
        # all 윈도우(2000~now) 는 2026-06-29 라이브 거래를 포함
        assert j["n_signals"] == 2
        assert j["n_trades_closed"] == 1
        assert j["open_positions"] == 1

    def test_endpoint_merges_sim_and_live(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app_mod, "_swing_repo_root", lambda: tmp_path)
        _swing_live_fixture(tmp_path)
        self._patch_sim(monkeypatch, tmp_path, sim_trades=[
            _sim_trade(symbol="FILUSDT",
                       entry_ts="2026-06-29T03:00:00+00:00", ret=4.0),
            _sim_trade(symbol="ETCUSDT",
                       entry_ts="2026-06-29T06:00:00+00:00", ret=-2.0),
        ])
        j = _C.get("/api/swing_live?window=all&refresh=1").json()
        assert "error" not in j
        assert j["sim"]["n"] == 2
        assert j["sim_trades_total"] == 2
        # 병합 trades 에 sim 행 포함 + 라이브 보존
        assert any(t["source"] == "sim" for t in j["trades"])
        assert any(t["source"] == "live" for t in j["trades"])
        assert j["n_trades_closed"] == 1
        assert j["has_data"] is True

    def test_endpoint_sim_only_not_empty(self, tmp_path, monkeypatch):
        # 라이브 WAL 없음 + sim 만 있어도 윈도우 비지 않음 (요구사항 검증)
        monkeypatch.setattr(app_mod, "_swing_repo_root", lambda: tmp_path)
        self._patch_sim(monkeypatch, tmp_path, sim_trades=[
            _sim_trade(symbol="FILUSDT",
                       entry_ts="2026-06-29T03:00:00+00:00", ret=2.5),
        ])
        j = _C.get("/api/swing_live?window=all&refresh=1").json()
        assert j["wal_files_count"] == 0
        assert j["sim"]["n"] == 1
        assert j["has_data"] is True
        assert len(j["trades"]) == 1
        assert j["trades"][0]["source"] == "sim"

    def test_endpoint_unknown_window_400(self):
        r = _C.get("/api/swing_live?window=bogus")
        assert r.status_code == 400
        assert "unknown window" in r.json()["error"]

    def test_endpoint_empty_graceful(self, tmp_path, monkeypatch):
        # 빈 repo (WAL·sim 모두 없음) → 깨끗한 빈 집계 (에러/스피너 아님)
        monkeypatch.setattr(app_mod, "_swing_repo_root", lambda: tmp_path)
        self._patch_sim(monkeypatch, tmp_path)
        j = _C.get("/api/swing_live?window=yesterday&refresh=1").json()
        assert "error" not in j
        assert j["n_signals"] == 0
        assert j["trades"] == []
        assert j["has_data"] is False
        assert j["wal_files_count"] == 0


# ── 동적 유니버스 (data/cache/binance_1h/*.parquet 전체, BTCUSDT 제외) ─────────

class TestSwingUniverse:
    @staticmethod
    def _mk_cache_dir(tmp_path, symbols):
        d = tmp_path / "data" / "cache" / "binance_1h"
        d.mkdir(parents=True, exist_ok=True)
        for s in symbols:
            (d / f"{s}.parquet").write_bytes(b"")
        return d

    def test_globs_and_excludes_btc_sorted(self, tmp_path, monkeypatch):
        self._mk_cache_dir(tmp_path, ["DOTUSDT", "ATOMUSDT", "BTCUSDT", "ETHUSDT"])
        monkeypatch.setattr(app_mod, "_swing_repo_root", lambda: tmp_path)
        syms = app_mod._swing_universe_symbols()
        assert "BTCUSDT" not in syms                 # 게이트는 제외
        assert syms == ["ATOMUSDT", "DOTUSDT", "ETHUSDT"]  # 정렬

    def test_fallback_when_no_cache_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app_mod, "_swing_repo_root", lambda: tmp_path)
        assert app_mod._swing_universe_symbols() == app_mod.SWING_UNIVERSE

    def test_hash_stable_and_universe_sensitive(self):
        a = app_mod._swing_universe_hash(["A", "B"])
        b = app_mod._swing_universe_hash(["A", "B"])
        c = app_mod._swing_universe_hash(["A", "B", "C"])
        assert a == b and len(a) == 12
        assert a != c  # 유니버스 바뀌면 해시 변동 → 캐시 자동 무효화

    def test_cache_path_includes_universe_hash(self, tmp_path, monkeypatch):
        self._mk_cache_dir(tmp_path, ["ATOMUSDT", "DOTUSDT"])
        monkeypatch.setattr(app_mod, "_swing_repo_root", lambda: tmp_path)
        monkeypatch.setattr(app_mod, "_SWING_SIM_CACHE", None)
        cache = app_mod._get_swing_sim_cache()
        h = app_mod._swing_universe_hash(["ATOMUSDT", "DOTUSDT"])
        assert h in str(cache.path)
        assert "sim_cache_" in str(cache.path)


# ── gross 합% vs net% (수수료 전/후, 둘 다 노출) ──────────────────────────────

class TestSwingGrossVsNet:
    def test_gross_and_net_distinct(self):
        sim = [
            _sim_trade(symbol="A", entry_ts="2026-06-29T01:00:00+00:00", ret=3.0),
            _sim_trade(symbol="B", entry_ts="2026-06-29T02:00:00+00:00", ret=-1.0),
            _sim_trade(symbol="C", entry_ts="2026-06-29T03:00:00+00:00", ret=2.0),
        ]
        s = aggregate_swing_window([], sim, _WIN_SINCE, _WIN_UNTIL)["sim"]
        # gross = 3 − 1 + 2 = 4.0 (pct 단순합 = 사용자가 표에서 더한 값)
        assert s["sum_pct"] == pytest.approx(4.0)
        # net = gross − 0.10%/거래 × 3 = 3.7
        assert s["net_pct"] == pytest.approx(4.0 - 0.10 * 3)
        # 둘이 달라야 혼동 없음 (gross +4.0 / net +3.7 동시 노출)
        assert abs(s["sum_pct"] - s["net_pct"]) == pytest.approx(0.30)
        assert s["win_rate"] == pytest.approx(2 / 3)


# ── 커스텀 날짜 범위 (start/end, KST, end 포함) ───────────────────────────────

class TestSwingLiveCustomRange:
    @staticmethod
    def _patch(monkeypatch, tmp_path, sim_trades):
        cache = SwingSimCache(tmp_path / "sim_cache.jsonl")
        cache.put_many(sim_trades)
        monkeypatch.setattr(app_mod, "_SWING_SIM_CACHE", cache)
        monkeypatch.setattr(app_mod, "_swing_compute_all_trades", lambda: [])
        monkeypatch.setattr(app_mod, "_swing_repo_root", lambda: tmp_path)

    def test_custom_range_filters_by_entry(self, tmp_path, monkeypatch):
        # A(05-02)·B(06-24)·C(06-29) → 커스텀 05-02~06-24 면 A,B 만
        self._patch(monkeypatch, tmp_path, sim_trades=[
            _sim_trade(symbol="AUSDT", entry_ts="2026-05-02T05:00:00+00:00", ret=1.0),
            _sim_trade(symbol="BUSDT", entry_ts="2026-06-24T05:00:00+00:00", ret=2.0),
            _sim_trade(symbol="CUSDT", entry_ts="2026-06-29T05:00:00+00:00", ret=3.0),
        ])
        j = _C.get("/api/swing_live?start=2026-05-02&end=2026-06-24&refresh=1").json()
        assert "error" not in j
        assert j["window"] == "custom"
        assert j["custom_start"] == "2026-05-02"
        assert j["custom_end"] == "2026-06-24"
        assert j["sim"]["n"] == 2
        assert {t["symbol"] for t in j["trades"]} == {"AUSDT", "BUSDT"}

    def test_custom_end_is_inclusive(self, tmp_path, monkeypatch):
        # end(06-24) 23:00 KST(=14:00 UTC) 포함, 다음날 00:30 KST(=15:30 UTC) 제외
        self._patch(monkeypatch, tmp_path, sim_trades=[
            _sim_trade(symbol="INUSDT", entry_ts="2026-06-24T14:00:00+00:00", ret=1.0),
            _sim_trade(symbol="OUTUSDT", entry_ts="2026-06-24T15:30:00+00:00", ret=1.0),
        ])
        j = _C.get("/api/swing_live?start=2026-06-24&end=2026-06-24&refresh=1").json()
        syms = {t["symbol"] for t in j["trades"]}
        assert "INUSDT" in syms
        assert "OUTUSDT" not in syms
        assert j["sim"]["n"] == 1

    def test_custom_bad_date_400(self):
        r = _C.get("/api/swing_live?start=2026-13-99&end=2026-06-24")
        assert r.status_code == 400
        assert "YYYY-MM-DD" in r.json()["error"]

    def test_custom_end_before_start_400(self):
        r = _C.get("/api/swing_live?start=2026-06-24&end=2026-05-01")
        assert r.status_code == 400
        assert "<" in r.json()["error"]

    def test_only_start_falls_back_to_preset(self, tmp_path, monkeypatch):
        # start 만(end 없음) → 커스텀 아님, 프리셋 window 사용
        self._patch(monkeypatch, tmp_path, sim_trades=[
            _sim_trade(symbol="ZUSDT", entry_ts="2026-06-29T05:00:00+00:00", ret=1.0),
        ])
        j = _C.get("/api/swing_live?window=all&start=2026-05-02&refresh=1").json()
        assert j["window"] == "all"        # 커스텀 무시
        assert j["custom_start"] is None
