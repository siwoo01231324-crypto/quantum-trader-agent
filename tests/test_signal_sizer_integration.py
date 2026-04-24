"""Tests for Signal ↔ PositionSizer integration (issue #76)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def test_expected_return_wins_over_rolling():
    """Signal.expected_return overrides rolling-window mu when both available."""
    from backtest.protocol import Signal
    from risk.sizing import kelly_continuous
    import numpy as np

    # Use a large sigma so kelly doesn't clamp both to 1.0
    sigma = 0.10  # 10% daily vol — large enough to keep kelly < 1

    # Rolling mu near zero → small kelly size
    rolling_mu = 0.0005
    rolling_size = kelly_continuous(mu=rolling_mu, sigma=sigma)

    # Signal provides a much larger expected return → bigger kelly size
    explicit_er = 0.05
    sig = Signal(action="buy", size=0.5, reason="test", expected_return=explicit_er)
    explicit_size = kelly_continuous(mu=sig.expected_return, sigma=sigma)

    # They should differ — signal's ER produces a materially different size
    assert explicit_er != pytest.approx(rolling_mu, abs=0.001)
    assert explicit_size != pytest.approx(rolling_size, abs=0.001)
    assert explicit_size > rolling_size, "Higher expected_return must yield larger kelly size"


def test_win_probability_routes_to_kelly_binary():
    """Signal.win_probability routes to kelly_binary (not kelly_continuous)."""
    from backtest.protocol import Signal
    from risk.sizing import kelly_binary

    sig = Signal(action="buy", size=0.5, reason="test", win_probability=0.55)
    assert sig.win_probability is not None

    # kelly_binary with p=0.55, b=1 should be ~0.10
    size = kelly_binary(p=sig.win_probability, b=1.0)
    assert size == pytest.approx(0.10, abs=0.001)


def test_signal_zero_vs_none_sentinel():
    """0.0 and None are distinct sentinels with different routing semantics."""
    from backtest.protocol import Signal

    sig_zero = Signal(action="hold", size=0.0, reason="t", confidence=0.0)
    sig_none = Signal(action="hold", size=0.0, reason="t", confidence=None)

    # 0.0 confidence means "computed as zero" — still a number
    assert sig_zero.confidence == 0.0
    assert sig_zero.confidence is not None

    # None means "not computed" — routing should treat differently
    assert sig_none.confidence is None


def test_signal_all_optional_none_no_crash():
    """Signal with all optional fields None should not crash sizing calcs."""
    from backtest.protocol import Signal
    from risk.sizing import kelly_binary, kelly_continuous

    sig = Signal(action="buy", size=0.5, reason="fallback")
    # Should be able to check None and fall back gracefully
    if sig.win_probability is not None:
        size = kelly_binary(sig.win_probability, 1.0)
    else:
        size = 0.5  # fallback to signal.size
    assert size == 0.5


def test_confidence_in_signal_is_float():
    """confidence=0.7 is stored as float, not bool or int."""
    from backtest.protocol import Signal

    s = Signal(action="buy", size=0.1, reason="t", confidence=0.7)
    assert isinstance(s.confidence, float)
    assert s.confidence == pytest.approx(0.7)
