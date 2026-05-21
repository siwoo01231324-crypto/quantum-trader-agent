"""Tests for src/signals/trendlines.py — pivot + trendline + breakout target."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def test_pivots_detect_simple_peak_and_trough():
    from signals.trendlines import compute_pivots

    # 5-bar lookback. peak 가 idx=10, trough 가 idx=20 에 위치.
    high = pd.Series(
        [100.0] * 5
        + list(np.linspace(100, 150, 5))   # 5..10 상승
        + list(np.linspace(150, 100, 6))[1:]   # 11..15 하락
        + [100.0] * 5
    )
    low = high - 5
    out = compute_pivots(high, low, lookback=4)
    # peak 이 idx 10 부근에서 잡혀야
    peak_idxs = out.index[out["pivot_high"]].tolist()
    assert 10 in peak_idxs or 9 in peak_idxs or 11 in peak_idxs


def test_pivots_length_matches_input():
    from signals.trendlines import compute_pivots

    rng = np.random.default_rng(0)
    high = pd.Series(100 + rng.normal(0, 2, 100).cumsum())
    low = high - 1
    out = compute_pivots(high, low, lookback=5)
    assert len(out) == 100
    assert list(out.columns) == ["pivot_high", "pivot_low"]


def test_swing_levels_returns_sorted_desc():
    from signals.trendlines import compute_swing_levels

    # 다양한 swing 만들기 — 사인파
    n = 200
    x = np.linspace(0, 8 * np.pi, n)
    high = pd.Series(100 + 10 * np.sin(x) + 1)
    low = pd.Series(100 + 10 * np.sin(x) - 1)
    levels = compute_swing_levels(high, low, lookback=3, max_levels=10)
    assert isinstance(levels, list)
    assert len(levels) <= 10
    # 내림차순
    assert levels == sorted(levels, reverse=True)


def test_trendline_breakout_returns_proper_shape():
    """compute_trendline_breakout 결과 shape · column · NaN 안전성."""
    from signals.trendlines import compute_trendline_breakout

    # 사인파 — 다양한 pivot 형성
    n = 300
    x = np.linspace(0, 10 * np.pi, n)
    close = pd.Series(100 + 10 * np.sin(x))
    high = close + 0.5
    low = close - 0.5
    out = compute_trendline_breakout(close, high, low, lookback=3)
    assert list(out.columns) == ["signal", "target_price"]
    assert len(out) == n
    # signal 은 None / breakout_up / breakout_down 중 하나
    valid = {None, "breakout_up", "breakout_down"}
    for s in out["signal"].tolist():
        assert s in valid
    # target_price 가 NaN 이 아닌 row 는 양수
    finite = out["target_price"].dropna()
    if len(finite) > 0:
        assert (finite > 0).all()


def test_find_recent_trendlines_format():
    from signals.trendlines import find_recent_trendlines

    n = 100
    x = np.linspace(0, 6 * np.pi, n)
    high = pd.Series(100 + 5 * np.sin(x) + 1)
    low = pd.Series(100 + 5 * np.sin(x) - 1)
    out = find_recent_trendlines(high, low, lookback=3, max_pairs=5)
    assert isinstance(out, list)
    for line in out:
        assert set(line.keys()) >= {"type", "x1", "y1", "x2", "y2"}
        assert line["type"] in ("up", "down")
        assert line["x2"] > line["x1"]


def test_trendline_breakout_invalid_lookback():
    from signals.trendlines import compute_trendline_breakout

    s = pd.Series([100.0] * 50)
    with pytest.raises(ValueError):
        compute_trendline_breakout(s, s + 1, s - 1, lookback=0)
