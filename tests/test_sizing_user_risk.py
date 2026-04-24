"""Tests for user_risk_vol_target (P1 risk_score parametric vol target).

AC:
  user_risk_vol_target(0.0) == 0.05
  user_risk_vol_target(1.0) == 0.20
  user_risk_vol_target(0.5) == 0.125
  Invalid risk_score raises ValueError.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from risk.sizing import user_risk_vol_target  # noqa: E402


def test_user_risk_vol_target_floor():
    """risk_score=0.0 -> vol_floor (0.05)."""
    assert user_risk_vol_target(0.0) == pytest.approx(0.05)


def test_user_risk_vol_target_ceil():
    """risk_score=1.0 -> vol_ceil (0.20)."""
    assert user_risk_vol_target(1.0) == pytest.approx(0.20)


def test_user_risk_vol_target_mid():
    """risk_score=0.5 -> linear midpoint = 0.05 + 0.5*(0.20-0.05) = 0.125."""
    assert user_risk_vol_target(0.5) == pytest.approx(0.125)


def test_user_risk_vol_target_custom_bounds():
    """Custom vol_floor / vol_ceil are respected."""
    result = user_risk_vol_target(0.5, vol_floor=0.10, vol_ceil=0.30)
    assert result == pytest.approx(0.10 + 0.5 * (0.30 - 0.10))


def test_user_risk_vol_target_rejects_invalid():
    """risk_score outside [0, 1] raises ValueError."""
    with pytest.raises(ValueError):
        user_risk_vol_target(-0.1)
    with pytest.raises(ValueError):
        user_risk_vol_target(1.1)


def test_user_risk_vol_target_rejects_invalid_bounds():
    """vol_floor >= vol_ceil raises ValueError."""
    with pytest.raises(ValueError):
        user_risk_vol_target(0.5, vol_floor=0.20, vol_ceil=0.10)
    with pytest.raises(ValueError):
        user_risk_vol_target(0.5, vol_floor=0.15, vol_ceil=0.15)
