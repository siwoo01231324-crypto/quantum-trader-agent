"""Unit tests for AirborneSimCache (sim outcome dedup + persistence)."""
from __future__ import annotations

import pytest

from src.dashboard.airborne_sim_cache import AirborneSimCache


@pytest.fixture
def cache(tmp_path):
    return AirborneSimCache(tmp_path / "sim_cache.jsonl")


def _fire(symbol="BTCUSDT", side="long", ts="2026-05-27T07:00:33+00:00"):
    return {
        "ts": ts, "symbol": symbol, "side": side,
        "fire_close": 100.0, "trigger": 99.5,
    }


def _sim(symbol="BTCUSDT", side="long", ts="2026-05-27T07:00:33+00:00",
         outcome="TP", pct=1.0, bar_idx=1):
    return {
        **_fire(symbol, side, ts),
        "outcome": outcome, "pct": pct, "bar_idx": bar_idx,
    }


class TestSplit:
    def test_initial_all_missing(self, cache):
        fires = [_fire("BTCUSDT"), _fire("ETHUSDT")]
        cached, missing = cache.split(fires)
        assert cached == []
        assert len(missing) == 2

    def test_after_put_split_hits_cache(self, cache):
        cache.put_many([_sim("BTCUSDT"), _sim("ETHUSDT")])
        fires = [_fire("BTCUSDT"), _fire("ETHUSDT"), _fire("SOLUSDT")]
        cached, missing = cache.split(fires)
        assert len(cached) == 2
        assert len(missing) == 1
        assert missing[0]["symbol"] == "SOLUSDT"
        assert cached[0]["outcome"] == "TP"

    def test_side_disambiguation(self, cache):
        """long 캐시됨 → short 는 cache miss."""
        cache.put_many([_sim("BTCUSDT", side="long")])
        cached, missing = cache.split([
            _fire("BTCUSDT", side="long"),
            _fire("BTCUSDT", side="short"),
        ])
        assert len(cached) == 1
        assert len(missing) == 1
        assert missing[0]["side"] == "short"

    def test_skips_malformed_fires(self, cache):
        cached, missing = cache.split([
            {"ts": "", "symbol": "X", "side": "long"},
            {"ts": "t", "symbol": "", "side": "long"},
            _fire("BTCUSDT"),
        ])
        # 한 건만 valid
        assert len(missing) == 1
        assert missing[0]["symbol"] == "BTCUSDT"


class TestPutMany:
    def test_appends_dedup(self, cache):
        added1 = cache.put_many([_sim("BTCUSDT"), _sim("ETHUSDT")])
        added2 = cache.put_many([_sim("BTCUSDT"), _sim("SOLUSDT")])
        # 두 번째 호출: BTCUSDT 는 dedup, SOLUSDT 만 추가
        assert added1 == 2
        assert added2 == 1
        assert cache.count() == 3

    def test_skips_malformed(self, cache):
        added = cache.put_many([
            {"ts": "", "symbol": "X", "side": "long"},
            {"ts": "t", "symbol": "BTC", "side": ""},
        ])
        assert added == 0


class TestPersistence:
    def test_reopen_preserves_cache(self, tmp_path):
        c1 = AirborneSimCache(tmp_path / "sim.jsonl")
        c1.put_many([_sim("BTCUSDT"), _sim("ETHUSDT")])
        # 새 instance 로 reopen
        c2 = AirborneSimCache(tmp_path / "sim.jsonl")
        cached, missing = c2.split([_fire("BTCUSDT")])
        assert len(cached) == 1
        assert c2.count() == 2

    def test_corrupted_line_skipped(self, tmp_path):
        path = tmp_path / "sim.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            f.write('{"ts":"a","symbol":"BTC","side":"long","outcome":"TP"}\n')
            f.write('garbage line\n')
            f.write('{"ts":"b","symbol":"ETH","side":"short","outcome":"SL"}\n')
        c = AirborneSimCache(path)
        assert c.count() == 2
