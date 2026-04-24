"""Tests for StabilityGrade pure function (P3 — patent avoidance, §Phase 4)."""
import pytest
from src.universe import StabilityGrade, grade_symbol


def test_stability_grade_A():
    """BTC-scale asset should grade as A."""
    sg = StabilityGrade()
    result = sg.grade(mcap_usd=1e12, vol_30d_usd=5e10, dev_activity=500)
    assert result == "A"


def test_stability_grade_F():
    """Micro-cap, no liquidity, no dev activity should grade as F."""
    sg = StabilityGrade()
    result = sg.grade(mcap_usd=1e5, vol_30d_usd=1e3, dev_activity=0)
    assert result == "F"


def test_stability_grade_C():
    """Mid-cap asset should grade as C or D (deterministic)."""
    sg = StabilityGrade()
    result = sg.grade(mcap_usd=5e8, vol_30d_usd=1e7, dev_activity=50)
    assert result in ("C", "D"), f"Expected C or D, got {result}"


def test_stability_grade_dev_activity_none_reweight():
    """dev_activity=None reweights to mcap:0.5 / volume:0.5 without crashing.

    Result should stay in the same grade category as the dev_activity-provided case
    for the mid-cap scenario (both in C/D range).
    """
    sg = StabilityGrade()
    with_dev = sg.grade(mcap_usd=5e8, vol_30d_usd=1e7, dev_activity=50)
    without_dev = sg.grade(mcap_usd=5e8, vol_30d_usd=1e7, dev_activity=None)
    # Both should be in the same broad category (C or D)
    assert with_dev in ("C", "D")
    assert without_dev in ("C", "D")


def test_stability_grade_boundary_values():
    """mcap=0, vol_30d=0 should grade as F."""
    sg = StabilityGrade()
    result = sg.grade(mcap_usd=0.0, vol_30d_usd=0.0, dev_activity=None)
    assert result == "F"


def test_grade_symbol_function():
    """grade_symbol convenience function should match StabilityGrade.grade."""
    sg = StabilityGrade()
    assert grade_symbol(1e12, 5e10, 500) == sg.grade(1e12, 5e10, 500)
    assert grade_symbol(1e5, 1e3, 0) == sg.grade(1e5, 1e3, 0)
    assert grade_symbol(5e8, 1e7) == sg.grade(5e8, 1e7)
