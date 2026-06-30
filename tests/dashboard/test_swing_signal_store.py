"""SwingSignalStore 단위 테스트 — 전수 저장 + 봉당 dedup + 윈도우 read."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.dashboard.swing_signal_store import SwingSignalStore, _floor_4h

UTC = timezone.utc


def test_append_and_load(tmp_path):
    s = SwingSignalStore(tmp_path / "signals.jsonl")
    t = datetime(2026, 6, 30, 14, 23, tzinfo=UTC)
    assert s.append("live-capitulation-bounce", "MANAUSDT",
                    stop_loss_pct=0.0178, take_profit_pct=0.0355, ts=t) is True
    rows = s.load_since(datetime(2026, 6, 30, tzinfo=UTC))
    assert len(rows) == 1
    assert rows[0]["strategy"] == "live-capitulation-bounce"
    assert rows[0]["symbol"] == "MANAUSDT"
    assert rows[0]["stop_loss_pct"] == pytest.approx(0.0178)
    assert rows[0]["take_profit_pct"] == pytest.approx(0.0355)


def test_dedup_same_4h_bar(tmp_path):
    # 같은 4h봉 내 반복 신호(매분)는 1건만 저장 — 스팸 방지.
    s = SwingSignalStore(tmp_path / "signals.jsonl")
    t = datetime(2026, 6, 30, 12, 5, tzinfo=UTC)  # 12:00 봉
    assert s.append("live-capitulation-bounce", "MANAUSDT", ts=t) is True
    assert s.append("live-capitulation-bounce", "MANAUSDT",
                    ts=t + timedelta(minutes=3)) is False   # 같은 봉 → skip
    assert s.append("live-capitulation-bounce", "MANAUSDT",
                    ts=t + timedelta(hours=4)) is True       # 다음 봉(16:00) → 저장
    assert len(s.load_since(datetime(2026, 6, 30, tzinfo=UTC))) == 2


def test_dedup_distinct_symbol_and_strategy(tmp_path):
    s = SwingSignalStore(tmp_path / "signals.jsonl")
    t = datetime(2026, 6, 30, 8, 1, tzinfo=UTC)
    assert s.append("live-capitulation-bounce", "MANAUSDT", ts=t) is True
    assert s.append("live-capitulation-bounce", "ATOMUSDT", ts=t) is True   # 다른 종목
    assert s.append("live-donchian-breakout-btcgate", "MANAUSDT", ts=t) is True  # 다른 전략
    assert len(s.load_since(datetime(2026, 6, 30, tzinfo=UTC))) == 3


def test_floor_4h():
    assert _floor_4h(datetime(2026, 6, 30, 14, 23, tzinfo=UTC)).hour == 12
    assert _floor_4h(datetime(2026, 6, 30, 3, 59, tzinfo=UTC)).hour == 0
    assert _floor_4h(datetime(2026, 6, 30, 20, 0, tzinfo=UTC)).hour == 20


def test_load_since_window(tmp_path):
    s = SwingSignalStore(tmp_path / "signals.jsonl")
    s.append("live-capitulation-bounce", "OLDUSDT",
             ts=datetime(2026, 6, 28, 12, 0, tzinfo=UTC))
    s.append("live-capitulation-bounce", "NEWUSDT",
             ts=datetime(2026, 6, 30, 12, 0, tzinfo=UTC))
    rows = s.load_since(datetime(2026, 6, 29, tzinfo=UTC))
    assert [r["symbol"] for r in rows] == ["NEWUSDT"]


def test_append_failsoft_persists_across_instances(tmp_path):
    p = tmp_path / "signals.jsonl"
    SwingSignalStore(p).append("live-capitulation-bounce", "MANAUSDT",
                               ts=datetime(2026, 6, 30, 12, 0, tzinfo=UTC))
    # 새 인스턴스가 디스크에서 dedup 복원 → 같은 봉 재append skip
    s2 = SwingSignalStore(p)
    assert s2.append("live-capitulation-bounce", "MANAUSDT",
                     ts=datetime(2026, 6, 30, 12, 30, tzinfo=UTC)) is False
    assert s2.count() == 1
