"""Unit tests for AirborneFireStore (JSONL append + dedup + window load)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.dashboard.airborne_fire_store import AirborneFireStore


@pytest.fixture
def store(tmp_path):
    return AirborneFireStore(tmp_path / "history.jsonl")


class TestAppendAndDedup:
    def test_initial_empty(self, store):
        assert store.count() == 0
        assert store.earliest_ts() is None

    def test_append_one(self, store):
        rec = {
            "ts": "2026-05-27T07:00:33+00:00",
            "symbol": "BTCUSDT", "side": "long",
            "fire_close": 65000.0, "trigger": 64900.0,
        }
        added = store.append_many([rec])
        assert added == 1
        assert store.count() == 1
        assert store.earliest_ts() == "2026-05-27T07:00:33+00:00"

    def test_dedup_same_key(self, store):
        rec = {
            "ts": "2026-05-27T07:00:33+00:00",
            "symbol": "BTCUSDT", "side": "long",
            "fire_close": 65000, "trigger": 64900,
        }
        store.append_many([rec])
        added = store.append_many([rec, rec, rec])
        assert added == 0
        assert store.count() == 1

    def test_dedup_different_sides_separate(self, store):
        ts = "2026-05-27T07:00:33+00:00"
        store.append_many([
            {"ts": ts, "symbol": "BTCUSDT", "side": "long",
             "fire_close": 1, "trigger": 1},
            {"ts": ts, "symbol": "BTCUSDT", "side": "short",
             "fire_close": 1, "trigger": 1},
        ])
        assert store.count() == 2

    def test_missing_fields_skipped(self, store):
        added = store.append_many([
            {"ts": "", "symbol": "X", "side": "long"},
            {"ts": "2026-05-27T07:00:33+00:00", "symbol": "", "side": "long"},
            {"ts": "2026-05-27T07:00:33+00:00", "symbol": "X", "side": ""},
        ])
        assert added == 0
        assert store.count() == 0


class TestPersistence:
    def test_reopen_keeps_dedup(self, tmp_path):
        path = tmp_path / "history.jsonl"
        s1 = AirborneFireStore(path)
        s1.append_many([{
            "ts": "2026-05-27T07:00:33+00:00",
            "symbol": "BTCUSDT", "side": "long",
            "fire_close": 1, "trigger": 1,
        }])
        # 재오픈 후 dedup 작동
        s2 = AirborneFireStore(path)
        added = s2.append_many([{
            "ts": "2026-05-27T07:00:33+00:00",
            "symbol": "BTCUSDT", "side": "long",
            "fire_close": 1, "trigger": 1,
        }])
        assert added == 0
        assert s2.count() == 1

    def test_corrupted_line_skipped(self, tmp_path):
        path = tmp_path / "history.jsonl"
        # 좋은 라인 + 깨진 라인 직접 작성
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            f.write('{"ts":"2026-05-27T07:00:33+00:00","symbol":"BTC","side":"long","fire_close":1,"trigger":1}\n')
            f.write('not valid json\n')
            f.write('{"ts":"2026-05-27T08:00:33+00:00","symbol":"ETH","side":"short","fire_close":1,"trigger":1}\n')
        s = AirborneFireStore(path)
        assert s.count() == 2  # 깨진 라인은 dedup cache 에 안 들어감


class TestLoadSince:
    def test_window_filter(self, store):
        store.append_many([
            {"ts": "2026-05-26T07:00:00+00:00", "symbol": "A", "side": "long",
             "fire_close": 1, "trigger": 1},
            {"ts": "2026-05-27T07:00:00+00:00", "symbol": "B", "side": "long",
             "fire_close": 1, "trigger": 1},
            {"ts": "2026-05-28T07:00:00+00:00", "symbol": "C", "side": "long",
             "fire_close": 1, "trigger": 1},
        ])
        # since 2026-05-27 자정 → B, C 만
        since = datetime(2026, 5, 27, 0, 0, tzinfo=timezone.utc)
        out = store.load_since(since)
        symbols = [r["symbol"] for r in out]
        assert symbols == ["B", "C"]  # 시각 오름차순

    def test_naive_since_raises(self, store):
        with pytest.raises(ValueError, match="tz-aware"):
            store.load_since(datetime(2026, 1, 1))

    def test_empty_file_returns_empty(self, store):
        out = store.load_since(datetime(2020, 1, 1, tzinfo=timezone.utc))
        assert out == []


class TestEarliestTs:
    def test_earliest_with_unsorted_appends(self, store):
        store.append_many([
            {"ts": "2026-05-28T07:00:00+00:00", "symbol": "C", "side": "long",
             "fire_close": 1, "trigger": 1},
            {"ts": "2026-05-26T07:00:00+00:00", "symbol": "A", "side": "long",
             "fire_close": 1, "trigger": 1},
            {"ts": "2026-05-27T07:00:00+00:00", "symbol": "B", "side": "long",
             "fire_close": 1, "trigger": 1},
        ])
        assert store.earliest_ts() == "2026-05-26T07:00:00+00:00"


class TestConcurrency:
    """Single-process; just verify lock 가 RuntimeError 안 일으킴."""

    def test_append_then_count(self, store):
        for i in range(50):
            store.append_many([{
                "ts": f"2026-05-{27 + (i // 24):02d}T{i % 24:02d}:00:00+00:00",
                "symbol": f"SYM{i % 5}USDT", "side": "long" if i % 2 == 0 else "short",
                "fire_close": float(i), "trigger": float(i) - 0.1,
            }])
        # 일부는 dedup 일 수 있으니 ≤ 50
        assert 0 < store.count() <= 50
