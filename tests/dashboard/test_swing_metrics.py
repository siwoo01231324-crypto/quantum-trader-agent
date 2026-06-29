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
