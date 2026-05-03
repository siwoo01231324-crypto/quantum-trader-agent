"""Tests for src/ml/validation/cscv.py and pbo.py."""
from __future__ import annotations

import math

import numpy as np
import pytest

from src.ml.validation import (
    combinatorial_symmetric_cv,
    probability_of_backtest_overfitting,
)


def test_cscv_n_combinations() -> None:
    """n_groups=16 produces C(16, 8) = 12_870 combinations."""
    rng = np.random.default_rng(0)
    arr = rng.normal(size=(320, 8))  # 320 days, 8 strategies
    out = combinatorial_symmetric_cv(arr, n_groups=16)
    assert out["n_combinations"] == 12_870
    assert out["logits"].shape == (12_870,)
    assert out["rank_correlations"].shape == (12_870,)


def test_pbo_random_strategies_in_range() -> None:
    """N random-walk strategies should produce PBO in a reasonable band."""
    rng = np.random.default_rng(42)
    arr = rng.normal(size=(2000, 8)) * 0.01
    pbo = probability_of_backtest_overfitting(arr, n_groups=16)
    assert 0.0 <= pbo <= 1.0
    # Random-walk strategies have no real edge — PBO should be near 0.5.
    # Allow a wide band so the test is robust to seed choice.
    assert 0.3 <= pbo <= 0.7


def test_pbo_one_dominant_strategy() -> None:
    """If one strategy dominates uniformly, PBO should be very small."""
    rng = np.random.default_rng(7)
    arr = rng.normal(size=(800, 8)) * 0.01
    arr[:, 0] += 0.05  # strategy 0 has a strong, consistent edge
    pbo = probability_of_backtest_overfitting(arr, n_groups=8)
    assert pbo < 0.2


def test_pbo_range_always_in_unit_interval() -> None:
    """PBO is a probability and must lie in [0, 1] for any input."""
    rng = np.random.default_rng(11)
    for _ in range(5):
        arr = rng.normal(size=(160, 4))
        pbo = probability_of_backtest_overfitting(arr, n_groups=8)
        assert 0.0 <= pbo <= 1.0


def test_cscv_returns_logits_finite() -> None:
    rng = np.random.default_rng(3)
    arr = rng.normal(size=(200, 4))
    out = combinatorial_symmetric_cv(arr, n_groups=8)
    assert np.all(np.isfinite(out["logits"]))


def test_cscv_invalid_n_groups_odd() -> None:
    arr = np.zeros((100, 4))
    with pytest.raises(ValueError):
        combinatorial_symmetric_cv(arr, n_groups=7)


def test_cscv_invalid_too_few_strategies() -> None:
    arr = np.zeros((100, 1))
    with pytest.raises(ValueError):
        combinatorial_symmetric_cv(arr, n_groups=8)


def test_cscv_invalid_t_smaller_than_groups() -> None:
    arr = np.zeros((4, 4))
    with pytest.raises(ValueError):
        combinatorial_symmetric_cv(arr, n_groups=8)


def test_cscv_2d_input_required() -> None:
    arr = np.zeros((100,))
    with pytest.raises(ValueError):
        combinatorial_symmetric_cv(arr, n_groups=8)
