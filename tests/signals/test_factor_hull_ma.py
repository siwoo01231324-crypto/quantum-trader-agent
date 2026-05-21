"""Tests for src/signals/hull_ma.py — Hull Moving Average + crossover."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest


def test_hma_basic_warmup_then_values():
    from signals.hull_ma import compute_hull_ma

    # 20 bars, length=4 → half=2, sqrt=2
    s = pd.Series(np.arange(20, dtype=float))
    out = compute_hull_ma(s, length=4)
    # warmup = length + sqrt(length) - 1 ≈ 5 → 첫 4~5 bars NaN
    assert np.isnan(out.iloc[0])
    # 후반 bars 는 finite + monotone increasing series 이므로 단조 증가 또는 동일
    tail = out.dropna().to_numpy()
    assert len(tail) > 5
    assert np.all(np.diff(tail) >= -1e-9)  # 단조 비감소


def test_hma_length_matches_input():
    from signals.hull_ma import compute_hull_ma

    s = pd.Series(np.arange(100, dtype=float))
    assert len(compute_hull_ma(s, length=21)) == 100


def test_hma_lag_smaller_than_sma():
    """HMA 의 핵심 속성 — 동일 length SMA 보다 추세 전환에 더 빨리 반응."""
    from signals.hull_ma import compute_hull_ma

    # 처음 30 bar 는 100, 다음 30 bar 는 200 으로 jump
    s = pd.Series([100.0] * 30 + [200.0] * 30)
    hma = compute_hull_ma(s, length=10)
    sma = s.rolling(10).mean()
    # bar 40 (jump 후 10 bar) 에서 HMA 가 SMA 보다 200 에 가까워야
    assert hma.iloc[40] > sma.iloc[40]


def test_hma_invalid_length():
    from signals.hull_ma import compute_hull_ma

    with pytest.raises(ValueError):
        compute_hull_ma(pd.Series([1.0, 2.0]), length=1)


class TestHullCross:
    def test_fast_must_be_less_than_slow(self):
        from signals.hull_ma import compute_hull_cross

        with pytest.raises(ValueError):
            compute_hull_cross(pd.Series([1.0] * 100), fast=21, slow=21)

    def test_returns_three_columns(self):
        from signals.hull_ma import compute_hull_cross

        out = compute_hull_cross(
            pd.Series(np.arange(200, dtype=float)),
            fast=5, slow=10,
        )
        assert list(out.columns) == ["hma_fast", "hma_slow", "signal"]
        assert len(out) == 200

    def test_signal_emitted_on_crossover(self):
        """fast HMA 가 slow HMA 를 명확히 돌파 시 golden 또는 dead 발생."""
        from signals.hull_ma import compute_hull_cross

        # 충분히 큰 점프 (변곡점 명확) 로 합성 — fast 가 먼저 반응
        n = 200
        s = pd.Series(
            list(np.full(n // 2, 100.0))
            + list(np.linspace(100, 200, n // 2))
        )
        out = compute_hull_cross(s, fast=10, slow=30)
        sigs = out["signal"].dropna().tolist()
        # crossover 신호가 최소 1번은 등장 (golden — 점프 시점)
        assert len(sigs) >= 1
        assert sigs[0] in ("golden", "dead")
