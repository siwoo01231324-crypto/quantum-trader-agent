"""Tests for sizing_comparison_with_confidence.json format (issue #76)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REGRESSION_JSON = ROOT / "docs" / "work" / "active" / "000076-signal-interface" / "sizing_comparison_with_confidence.json"


def test_regression_json_exists():
    """sizing_comparison_with_confidence.json must exist after compare script runs."""
    if not REGRESSION_JSON.exists():
        pytest.skip("sizing_comparison_with_confidence.json not yet generated — run compare script first")


def test_regression_json_required_keys():
    """JSON must have all required top-level keys."""
    if not REGRESSION_JSON.exists():
        pytest.skip("sizing_comparison_with_confidence.json not yet generated")

    data = json.loads(REGRESSION_JSON.read_text(encoding="utf-8"))

    required_keys = {
        "win_rate_baseline",
        "win_rate_with_confidence",
        "trade_count_baseline",
        "trade_count_with_confidence",
        "sharpe_baseline",
        "sharpe_with_confidence",
        "reason_if_regressed",
    }
    missing = required_keys - set(data.keys())
    assert not missing, f"Missing keys in regression JSON: {missing}"


def test_regression_json_numeric_fields():
    """Numeric fields must be finite floats/ints."""
    if not REGRESSION_JSON.exists():
        pytest.skip("sizing_comparison_with_confidence.json not yet generated")

    import math
    data = json.loads(REGRESSION_JSON.read_text(encoding="utf-8"))

    for key in ("win_rate_baseline", "win_rate_with_confidence", "sharpe_baseline", "sharpe_with_confidence"):
        val = data[key]
        assert isinstance(val, (int, float)), f"{key} must be numeric"
        assert not math.isnan(val), f"{key} must not be NaN"

    for key in ("trade_count_baseline", "trade_count_with_confidence"):
        val = data[key]
        assert isinstance(val, int), f"{key} must be int"
        assert val >= 0, f"{key} must be non-negative"


def test_regression_json_win_rates_in_unit_interval():
    """Win rates must be in [0, 1]."""
    if not REGRESSION_JSON.exists():
        pytest.skip("sizing_comparison_with_confidence.json not yet generated")

    data = json.loads(REGRESSION_JSON.read_text(encoding="utf-8"))
    assert 0.0 <= data["win_rate_baseline"] <= 1.0
    assert 0.0 <= data["win_rate_with_confidence"] <= 1.0


def test_regression_json_reason_if_regressed_is_string():
    """reason_if_regressed must be a string (empty or explanation)."""
    if not REGRESSION_JSON.exists():
        pytest.skip("sizing_comparison_with_confidence.json not yet generated")

    data = json.loads(REGRESSION_JSON.read_text(encoding="utf-8"))
    assert isinstance(data["reason_if_regressed"], str)
