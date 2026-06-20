"""Unit tests for PppSimCache (split/put_many/count/persistence).

``MaCrossSimCache`` 테스트 미러 — dedup key 만 (ts, symbol, side).
"""
from __future__ import annotations

from src.dashboard.ppp_sim_cache import PppSimCache


def _sig(ts, symbol="WLDUSDT", side="long"):
    return {"ts": ts, "symbol": symbol, "side": side, "close": 0.65}


def _sim(ts, symbol="WLDUSDT", side="long", outcome="TP", pct=3.0, bar_idx=4):
    return {"ts": ts, "symbol": symbol, "side": side,
            "outcome": outcome, "pct": pct, "bar_idx": bar_idx}


def test_split_all_miss_then_hit(tmp_path):
    c = PppSimCache(tmp_path / "sim.jsonl")
    sigs = [_sig("2026-06-19T00:00:00+00:00"),
            _sig("2026-06-19T00:05:00+00:00", side="short")]
    cached, missing = c.split(sigs)
    assert cached == [] and len(missing) == 2
    # 시뮬 후 put → 다음 split 은 hit
    c.put_many([_sim("2026-06-19T00:00:00+00:00"),
                _sim("2026-06-19T00:05:00+00:00", side="short")])
    cached, missing = c.split(sigs)
    assert len(cached) == 2 and missing == []


def test_put_many_dedup(tmp_path):
    c = PppSimCache(tmp_path / "sim.jsonl")
    assert c.put_many([_sim("2026-06-19T00:00:00+00:00")]) == 1
    # same key → skip
    assert c.put_many([_sim("2026-06-19T00:00:00+00:00", outcome="SL")]) == 0
    # different side → new
    assert c.put_many([_sim("2026-06-19T00:00:00+00:00", side="short")]) == 1
    assert c.count() == 2


def test_key_requires_all_fields(tmp_path):
    c = PppSimCache(tmp_path / "sim.jsonl")
    # side 누락 → skip (key None)
    assert c.put_many([{"ts": "2026-06-19T00:00:00+00:00", "symbol": "WLDUSDT"}]) == 0
    assert c.count() == 0


def test_persistence_across_instances(tmp_path):
    p = tmp_path / "sim.jsonl"
    PppSimCache(p).put_many([_sim("2026-06-19T00:00:00+00:00")])
    # 새 instance 가 디스크에서 읽어 hit
    c2 = PppSimCache(p)
    cached, missing = c2.split([_sig("2026-06-19T00:00:00+00:00")])
    assert len(cached) == 1 and missing == []
    assert c2.count() == 1
