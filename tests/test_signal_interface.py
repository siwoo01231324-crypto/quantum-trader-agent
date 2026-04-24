"""Tests for Signal dataclass — 6-field interface (issue #76)."""
from __future__ import annotations

import pytest


def test_signal_defaults_none():
    """Optional fields default to None."""
    from backtest.protocol import Signal

    s = Signal(action="hold", size=0.0, reason="test")
    assert s.expected_return is None
    assert s.win_probability is None
    assert s.confidence is None


def test_signal_full_constructor_6_fields():
    """Full 6-field constructor works and stores all values."""
    from backtest.protocol import Signal

    s = Signal(
        action="buy",
        size=0.1,
        reason="test",
        expected_return=0.02,
        win_probability=0.55,
        confidence=0.7,
    )
    assert s.action == "buy"
    assert s.size == 0.1
    assert s.reason == "test"
    assert s.expected_return == pytest.approx(0.02)
    assert s.win_probability == pytest.approx(0.55)
    assert s.confidence == pytest.approx(0.7)


def test_signal_zero_sentinel_distinct_from_none():
    """0.0 confidence is different from None — sentinel distinction."""
    from backtest.protocol import Signal

    s_zero = Signal(action="hold", size=0.0, reason="t", confidence=0.0)
    s_none = Signal(action="hold", size=0.0, reason="t")
    assert s_zero.confidence == 0.0
    assert s_none.confidence is None
    assert s_zero.confidence is not s_none.confidence


def test_signal_hold_with_all_fields():
    """hold signal can carry optional fields without error."""
    from backtest.protocol import Signal

    s = Signal(
        action="hold",
        size=0.0,
        reason="no signal",
        expected_return=-0.01,
        win_probability=0.45,
        confidence=0.3,
    )
    assert s.action == "hold"
    assert s.expected_return == pytest.approx(-0.01)
