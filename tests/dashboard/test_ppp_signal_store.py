"""Unit tests for PppSignalStore (append/dedup/load_since)."""
from __future__ import annotations

from datetime import datetime, timezone

from src.dashboard.ppp_signal_store import PppSignalStore


def _sig(ts, symbol="WLDUSDT", side="long", **kw):
    base = {"ts": ts, "symbol": symbol, "side": side, "close": 0.65,
            "qpp_main": 22.0, "qpp_sig": 25.0, "choppiness": 63.0, "regime": "range"}
    base.update(kw)
    return base


def test_append_and_count(tmp_path):
    st = PppSignalStore(tmp_path / "h.jsonl")
    assert st.append_many([_sig("2026-06-19T00:00:00+00:00")]) == 1
    assert st.count() == 1


def test_dedup(tmp_path):
    st = PppSignalStore(tmp_path / "h.jsonl")
    st.append_many([_sig("2026-06-19T00:00:00+00:00")])
    # same (ts, symbol, side) → skipped
    assert st.append_many([_sig("2026-06-19T00:00:00+00:00")]) == 0
    # different side → new
    assert st.append_many([_sig("2026-06-19T00:00:00+00:00", side="short")]) == 1
    assert st.count() == 2


def test_load_since_filters_and_sorts(tmp_path):
    st = PppSignalStore(tmp_path / "h.jsonl")
    st.append_many([
        _sig("2026-06-18T00:00:00+00:00"),
        _sig("2026-06-19T12:00:00+00:00", symbol="ORDIUSDT"),
    ])
    since = datetime(2026, 6, 19, tzinfo=timezone.utc)
    out = st.load_since(since)
    assert len(out) == 1 and out[0]["symbol"] == "ORDIUSDT"


def test_load_since_requires_tzaware(tmp_path):
    st = PppSignalStore(tmp_path / "h.jsonl")
    import pytest
    with pytest.raises(ValueError):
        st.load_since(datetime(2026, 6, 19))


def test_persistence_across_instances(tmp_path):
    p = tmp_path / "h.jsonl"
    PppSignalStore(p).append_many([_sig("2026-06-19T00:00:00+00:00")])
    # new instance reads existing file for dedup
    st2 = PppSignalStore(p)
    assert st2.append_many([_sig("2026-06-19T00:00:00+00:00")]) == 0
    assert st2.count() == 1
