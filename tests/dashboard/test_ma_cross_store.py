"""Unit tests for MaCrossStore + MaCrossSimCache (cross persistence + dedup).

airborne_fire_store / airborne_sim_cache 테스트 미러. dedup key 가 cross
방향(golden/death) 기반인 점만 다르다.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.dashboard.ma_cross_sim_cache import MaCrossSimCache
from src.dashboard.ma_cross_store import MaCrossStore


def _cross(symbol="BTCUSDT", cross="golden", ts="2026-06-18T10:00:33+00:00"):
    return {
        "ts": ts, "symbol": symbol, "cross": cross,
        "close": 67000.0, "sma_fast": 66900.0, "sma_slow": 65000.0,
    }


def _sim(symbol="BTCUSDT", cross="golden", ts="2026-06-18T10:00:33+00:00",
         outcome="TP", pct=12.0, bar_idx=1):
    return {**_cross(symbol, cross, ts),
            "outcome": outcome, "pct": pct, "bar_idx": bar_idx}


# ── MaCrossStore ────────────────────────────────────────────────────────────

class TestMaCrossStore:
    @pytest.fixture
    def store(self, tmp_path):
        return MaCrossStore(tmp_path / "history.jsonl")

    def test_append_and_load_since(self, store):
        added = store.append_many([_cross("BTCUSDT"), _cross("ETHUSDT")])
        assert added == 2
        since = datetime(2026, 6, 18, tzinfo=timezone.utc)
        loaded = store.load_since(since)
        assert len(loaded) == 2
        assert {c["symbol"] for c in loaded} == {"BTCUSDT", "ETHUSDT"}

    def test_dedup_on_ts_symbol_cross(self, store):
        a1 = store.append_many([_cross("BTCUSDT", "golden")])
        # 같은 (ts, symbol, cross) → dedup
        a2 = store.append_many([_cross("BTCUSDT", "golden")])
        # 같은 종목·시각이라도 다른 방향이면 별개
        a3 = store.append_many([_cross("BTCUSDT", "death")])
        assert a1 == 1
        assert a2 == 0
        assert a3 == 1
        assert store.count() == 2

    def test_skips_malformed(self, store):
        added = store.append_many([
            {"ts": "", "symbol": "X", "cross": "golden"},
            {"ts": "t", "symbol": "", "cross": "golden"},
            {"ts": "t", "symbol": "X", "cross": ""},
        ])
        assert added == 0

    def test_load_since_filters_by_time(self, store):
        store.append_many([
            _cross("OLD", ts="2026-06-10T00:00:00+00:00"),
            _cross("NEW", ts="2026-06-18T00:00:00+00:00"),
        ])
        loaded = store.load_since(datetime(2026, 6, 15, tzinfo=timezone.utc))
        assert len(loaded) == 1
        assert loaded[0]["symbol"] == "NEW"

    def test_load_since_requires_tz_aware(self, store):
        with pytest.raises(ValueError):
            store.load_since(datetime(2026, 6, 18))  # naive

    def test_load_missing_file_returns_empty(self, tmp_path):
        s = MaCrossStore(tmp_path / "nope.jsonl")
        assert s.load_since(datetime(2026, 1, 1, tzinfo=timezone.utc)) == []

    def test_earliest_ts(self, store):
        assert store.earliest_ts() is None
        store.append_many([
            _cross("A", ts="2026-06-18T00:00:00+00:00"),
            _cross("B", ts="2026-06-10T00:00:00+00:00"),
        ])
        assert store.earliest_ts() == "2026-06-10T00:00:00+00:00"

    def test_reopen_preserves_dedup(self, tmp_path):
        s1 = MaCrossStore(tmp_path / "h.jsonl")
        s1.append_many([_cross("BTCUSDT")])
        s2 = MaCrossStore(tmp_path / "h.jsonl")
        # reopen 후 같은 cross → dedup
        assert s2.append_many([_cross("BTCUSDT")]) == 0
        assert s2.count() == 1

    def test_corrupted_line_skipped(self, tmp_path):
        path = tmp_path / "h.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            f.write('{"ts":"2026-06-18T00:00:00+00:00","symbol":"A","cross":"golden"}\n')
            f.write('garbage line\n')
            f.write('{"ts":"2026-06-18T01:00:00+00:00","symbol":"B","cross":"death"}\n')
        s = MaCrossStore(path)
        loaded = s.load_since(datetime(2026, 6, 17, tzinfo=timezone.utc))
        assert len(loaded) == 2


# ── MaCrossSimCache ─────────────────────────────────────────────────────────

class TestMaCrossSimCache:
    @pytest.fixture
    def cache(self, tmp_path):
        return MaCrossSimCache(tmp_path / "sim_cache.jsonl")

    def test_initial_all_missing(self, cache):
        cached, missing = cache.split([_cross("BTCUSDT"), _cross("ETHUSDT")])
        assert cached == []
        assert len(missing) == 2

    def test_after_put_split_hits_cache(self, cache):
        cache.put_many([_sim("BTCUSDT"), _sim("ETHUSDT")])
        cached, missing = cache.split([
            _cross("BTCUSDT"), _cross("ETHUSDT"), _cross("SOLUSDT")])
        assert len(cached) == 2
        assert len(missing) == 1
        assert missing[0]["symbol"] == "SOLUSDT"
        assert cached[0]["outcome"] == "TP"

    def test_cross_disambiguation(self, cache):
        """golden 캐시됨 → death 는 cache miss."""
        cache.put_many([_sim("BTCUSDT", cross="golden")])
        cached, missing = cache.split([
            _cross("BTCUSDT", cross="golden"),
            _cross("BTCUSDT", cross="death"),
        ])
        assert len(cached) == 1
        assert len(missing) == 1
        assert missing[0]["cross"] == "death"

    def test_put_many_dedup(self, cache):
        a1 = cache.put_many([_sim("BTCUSDT"), _sim("ETHUSDT")])
        a2 = cache.put_many([_sim("BTCUSDT"), _sim("SOLUSDT")])
        assert a1 == 2
        assert a2 == 1
        assert cache.count() == 3

    def test_skips_malformed(self, cache):
        added = cache.put_many([
            {"ts": "", "symbol": "X", "cross": "golden"},
            {"ts": "t", "symbol": "BTC", "cross": ""},
        ])
        assert added == 0

    def test_reopen_preserves_cache(self, tmp_path):
        c1 = MaCrossSimCache(tmp_path / "sim.jsonl")
        c1.put_many([_sim("BTCUSDT"), _sim("ETHUSDT")])
        c2 = MaCrossSimCache(tmp_path / "sim.jsonl")
        cached, missing = c2.split([_cross("BTCUSDT")])
        assert len(cached) == 1
        assert c2.count() == 2

    def test_corrupted_line_skipped(self, tmp_path):
        path = tmp_path / "sim.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            f.write('{"ts":"a","symbol":"BTC","cross":"golden","outcome":"TP"}\n')
            f.write('garbage line\n')
            f.write('{"ts":"b","symbol":"ETH","cross":"death","outcome":"SL"}\n')
        c = MaCrossSimCache(path)
        assert c.count() == 2
