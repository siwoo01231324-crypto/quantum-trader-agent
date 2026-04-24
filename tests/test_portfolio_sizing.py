"""Tests for portfolio.sizing.resolve_size (T3 — issue #78)."""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.protocol import Signal
from portfolio.sizing import resolve_size
from risk.sizing import kelly_binary, kelly_continuous, ewma_sigma


def _make_returns(n: int = 30, seed: int = 42) -> pd.Series:
    import numpy as np
    rng = numpy.random.default_rng(seed)
    return pd.Series(rng.normal(0.001, 0.02, n))


import numpy


def test_resolve_size_signal_wins_precedence():
    """expected_return not None → kelly_continuous path used."""
    signal = Signal(action="buy", size=0.5, reason="test", expected_return=0.05)
    recent = _make_returns(30)
    result = resolve_size(signal, recent)
    sigma = ewma_sigma(recent.dropna().values)
    expected = kelly_continuous(mu=0.05, sigma=sigma)
    assert result == pytest.approx(expected, abs=1e-9)


def test_resolve_size_none_returns_means_skip():
    """expected_return None and win_probability None → signal.size returned unchanged."""
    signal = Signal(action="buy", size=0.3, reason="test")
    recent = _make_returns(20)
    result = resolve_size(signal, recent)
    assert result == pytest.approx(0.3)


def test_resolve_size_parity_with_engine():
    """resolve_size output matches direct kelly_binary call for win_probability path."""
    p = 0.6
    signal = Signal(action="buy", size=0.9, reason="test", win_probability=p)
    result = resolve_size(signal, None)
    expected = kelly_binary(p=p, b=1.0)
    assert result == pytest.approx(expected, abs=1e-9)
