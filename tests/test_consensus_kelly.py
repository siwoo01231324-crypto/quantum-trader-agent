"""Tests for consensus_kelly (P5 signal-agreement-weighted Kelly fraction).

AC:
  consensus_kelly(1.0, 0.0, k_base=0.5, k_max=0.75) == fractional_kelly(1.0, 0.5)
  consensus_kelly(1.0, 1.0, k_base=0.5, k_max=0.75) == fractional_kelly(1.0, 0.75)
  consensus_kelly(1.0, 0.5, ...) == linear midpoint between base and max.
  momo_btc_v2 default use_consensus_kelly=False -> existing behaviour unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from risk.sizing import consensus_kelly, fractional_kelly  # noqa: E402


def test_zero_agreement_equals_base():
    """signal_agreement=0 -> effective k = k_base (0.5)."""
    result = consensus_kelly(1.0, 0.0)
    assert result == pytest.approx(fractional_kelly(1.0, 0.5))


def test_full_agreement_equals_max():
    """signal_agreement=1 -> effective k = k_max (0.75)."""
    result = consensus_kelly(1.0, 1.0)
    assert result == pytest.approx(fractional_kelly(1.0, 0.75))


def test_mid_agreement_linear():
    """signal_agreement=0.5 -> k = k_base + 0.5*(k_max - k_base) = 0.625."""
    result = consensus_kelly(1.0, 0.5)
    expected_k = 0.5 + 0.5 * (0.75 - 0.5)  # 0.625
    assert result == pytest.approx(fractional_kelly(1.0, expected_k))


def test_delegates_to_fractional_kelly():
    """consensus_kelly output matches explicit fractional_kelly call."""
    full_kelly = 0.4
    agreement = 0.8
    k_base, k_max = 0.5, 0.75
    effective_k = k_base + agreement * (k_max - k_base)
    assert consensus_kelly(full_kelly, agreement, k_base=k_base, k_max=k_max) == pytest.approx(
        fractional_kelly(full_kelly, effective_k)
    )


def test_custom_bounds():
    """Custom k_base / k_max are respected."""
    result = consensus_kelly(1.0, 0.0, k_base=0.25, k_max=1.0)
    assert result == pytest.approx(fractional_kelly(1.0, 0.25))

    result = consensus_kelly(1.0, 1.0, k_base=0.25, k_max=1.0)
    assert result == pytest.approx(fractional_kelly(1.0, 1.0))


def test_output_clamped_to_unit():
    """Output is always in [0, 1] regardless of full_kelly value."""
    result = consensus_kelly(10.0, 1.0, k_base=0.5, k_max=0.75)
    assert 0.0 <= result <= 1.0


def test_rejects_invalid_agreement():
    """signal_agreement outside [0, 1] raises ValueError."""
    with pytest.raises(ValueError):
        consensus_kelly(0.5, -0.1)
    with pytest.raises(ValueError):
        consensus_kelly(0.5, 1.1)


def test_rejects_invalid_k_bounds():
    """k_base >= k_max raises ValueError."""
    with pytest.raises(ValueError):
        consensus_kelly(0.5, 0.5, k_base=0.75, k_max=0.5)
    with pytest.raises(ValueError):
        consensus_kelly(0.5, 0.5, k_base=0.5, k_max=0.5)


def test_momo_btc_v2_use_consensus_kelly_default_false():
    """MomoBtcV2 default use_consensus_kelly=False -> sizing unchanged."""
    from backtest.strategies.momo_btc_v2 import MomoBtcV2

    strategy = MomoBtcV2()
    assert strategy.use_consensus_kelly is False
