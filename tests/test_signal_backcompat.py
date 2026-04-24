"""Tests for Signal backward compatibility (issue #76)."""
from __future__ import annotations


def test_legacy_signal_positional_3_args():
    """Legacy Signal(action, size, reason) must still work unchanged."""
    from backtest.protocol import Signal

    s = Signal("buy", 1.0, "bullish divergence")
    assert s.action == "buy"
    assert s.size == 1.0
    assert s.reason == "bullish divergence"
    assert s.expected_return is None
    assert s.win_probability is None
    assert s.confidence is None


def test_signal_field_order_snapshot():
    """Field order: action, size, reason, expected_return, win_probability, confidence."""
    import dataclasses
    from backtest.protocol import Signal

    fields = [f.name for f in dataclasses.fields(Signal)]
    assert fields[:3] == ["action", "size", "reason"]
    assert "expected_return" in fields
    assert "win_probability" in fields
    assert "confidence" in fields
    # Optional fields come after the 3 required ones
    assert fields.index("expected_return") > 2
    assert fields.index("win_probability") > 2
    assert fields.index("confidence") > 2


def test_legacy_hold_signal():
    """hold Signal(action, size, reason) with no optional fields."""
    from backtest.protocol import Signal

    s = Signal("hold", 0.0, "warmup")
    assert s.action == "hold"
    assert s.size == 0.0
    assert s.confidence is None


def test_legacy_sell_signal():
    """sell Signal backward compat."""
    from backtest.protocol import Signal

    s = Signal("sell", 1.0, "bearish divergence")
    assert s.action == "sell"
    assert s.size == 1.0
    assert s.win_probability is None
