"""Unit tests for scripts/ma_cross_alert_daemon — cross detection + dispatch + cooldown."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ma_cross_alert_daemon as daemon  # noqa: E402

FAST, SLOW = 5, 20  # 작은 윈도우로 synthetic 데이터 길이 절약 (로직은 25/200 과 동일)


def _series(values: list[float]) -> pd.Series:
    idx = pd.date_range("2026-01-01", periods=len(values), freq="1h", tz="UTC")
    return pd.Series(values, index=idx)


def _history(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="1h", tz="UTC")
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes,
         "volume": [1.0] * len(closes)},
        index=idx,
    )


# ── detect_cross ─────────────────────────────────────────────────────────────


def test_detect_cross_warmup_returns_none():
    # slow+2 봉 미만이면 None.
    assert daemon.detect_cross(_series([1.0] * (SLOW + 1)), FAST, SLOW) is None


def test_detect_cross_flat_no_cross():
    assert daemon.detect_cross(_series([100.0] * (SLOW + 5)), FAST, SLOW) is None


def test_detect_cross_golden():
    # 길게 하락 추세로 fast<slow 를 만든 뒤, 막판 급등으로 fast 가 slow 상향 돌파.
    closes = list(np.linspace(120, 80, SLOW + 3)) + [200.0]
    cross = daemon.detect_cross(_series(closes), FAST, SLOW)
    assert cross == daemon.CROSS_GOLDEN


def test_detect_cross_death():
    # 길게 상승 추세로 fast>slow 를 만든 뒤, 막판 급락으로 fast 가 slow 하향 돌파.
    closes = list(np.linspace(80, 120, SLOW + 3)) + [10.0]
    cross = daemon.detect_cross(_series(closes), FAST, SLOW)
    assert cross == daemon.CROSS_DEATH


def test_detect_cross_rejects_fast_ge_slow():
    import pytest
    with pytest.raises(ValueError):
        daemon.detect_cross(_series([1.0] * 50), SLOW, FAST)


# ── evaluate_and_dispatch ────────────────────────────────────────────────────


def test_dispatch_golden_calls_notify():
    closes = list(np.linspace(120, 80, SLOW + 3)) + [200.0]
    state = daemon.SymbolState()
    state.history_1h = _history(closes)

    captured: list = []

    def spy(level, title, body, fields):
        captured.append((level, title, body, fields))

    cross = daemon.evaluate_and_dispatch(
        symbol="BTCUSDT", state=state, fast=FAST, slow=SLOW,
        dry_run=False, notify_fn=spy,
    )
    assert cross == daemon.CROSS_GOLDEN
    assert len(captured) == 1
    level, title, body, fields = captured[0]
    assert level == "info"
    assert "골든크로스" in title
    assert "BTCUSDT" in title
    assert fields["type"] == daemon.CROSS_GOLDEN
    assert fields["timeframe"] == "1h"


def test_dispatch_no_cross_no_notify():
    state = daemon.SymbolState()
    state.history_1h = _history([100.0] * (SLOW + 5))
    captured: list = []
    cross = daemon.evaluate_and_dispatch(
        symbol="ETHUSDT", state=state, fast=FAST, slow=SLOW,
        dry_run=False, notify_fn=lambda *a: captured.append(a),
    )
    assert cross is None
    assert captured == []


def test_dispatch_cooldown_suppresses_repeat():
    closes = list(np.linspace(120, 80, SLOW + 3)) + [200.0]
    state = daemon.SymbolState()
    state.history_1h = _history(closes)
    calls: list = []

    spy = lambda *a: calls.append(a)
    first = daemon.evaluate_and_dispatch(
        symbol="BTCUSDT", state=state, fast=FAST, slow=SLOW,
        dry_run=False, notify_fn=spy,
    )
    # 같은 봉 재평가 → cooldown 으로 억제.
    second = daemon.evaluate_and_dispatch(
        symbol="BTCUSDT", state=state, fast=FAST, slow=SLOW,
        dry_run=False, notify_fn=spy,
    )
    assert first == daemon.CROSS_GOLDEN
    assert second is None
    assert len(calls) == 1


# ── helpers ──────────────────────────────────────────────────────────────────


def test_universe_diff():
    added, removed, unchanged = daemon.compute_universe_diff(
        ["A", "B", "C"], ["B", "C", "D"],
    )
    assert added == ["D"]
    assert removed == ["A"]
    assert unchanged == ["B", "C"]


def test_next_polling_wakeup_is_boundary_plus_30s():
    from datetime import datetime, timezone
    now = datetime(2026, 1, 1, 5, 0, 25, tzinfo=timezone.utc)
    nxt = daemon._next_polling_wakeup(now)
    assert (nxt.minute, nxt.second) == (0, 30)
    assert nxt.hour == 5
    # 경계 이후면 다음 시간으로.
    now2 = datetime(2026, 1, 1, 5, 0, 35, tzinfo=timezone.utc)
    assert daemon._next_polling_wakeup(now2).hour == 6
