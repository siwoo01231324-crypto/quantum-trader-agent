"""Tests for src/ml/scoring.py."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from ml.scoring import (
    annualized_sharpe,
    deflated_sharpe_ratio,
    max_drawdown,
    pr_auc_score,
    sharpe_improvement_ratio,
)


# ---------------------------------------------------------------------------
# annualized_sharpe
# ---------------------------------------------------------------------------

def _make_returns(n: int = 200, mean: float = 0.001, std: float = 0.02, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mean, std, n))


def test_annualized_sharpe_empty_returns_zero():
    assert annualized_sharpe(pd.Series([], dtype=float), periods_per_year=252) == 0.0


def test_annualized_sharpe_zero_std_returns_zero():
    returns = pd.Series([0.001] * 50)
    # All identical → std(ddof=1) == 0
    assert annualized_sharpe(returns, periods_per_year=252) == 0.0


def test_annualized_sharpe_ratio_scales_with_sqrt_periods():
    """annualized_sharpe(p1) / annualized_sharpe(p2) == sqrt(p1/p2)."""
    returns = _make_returns(1000)
    p1, p2 = 6552, 35040
    sr1 = annualized_sharpe(returns, periods_per_year=p1)
    sr2 = annualized_sharpe(returns, periods_per_year=p2)
    assert sr2 != 0.0, "sr2 must not be zero for ratio test"
    ratio = sr1 / sr2
    expected = math.sqrt(p1 / p2)
    assert abs(ratio - expected) < 1e-10


def test_annualized_sharpe_positive_drift():
    returns = pd.Series([0.01] * 10 + [-0.001] * 10)
    sr = annualized_sharpe(returns, periods_per_year=252)
    assert sr > 0.0


# ---------------------------------------------------------------------------
# deflated_sharpe_ratio
# ---------------------------------------------------------------------------

def test_dsr_n_trials_1_close_to_raw_sharpe_significance():
    """DSR(n_trials=1) should be close to Φ(SR / sqrt(Var_SR)).

    With n_trials=1 the expected maximum is 0 (no selection bias), so DSR
    equals the raw significance of the observed SR.
    """
    observed_sr = 1.5
    # Use a non-trivial pool of SR estimates (length = T determines Var_SR)
    sr_estimates = list(np.random.default_rng(42).normal(0.5, 0.3, 100))
    dsr_1 = deflated_sharpe_ratio(observed_sr, sr_estimates, n_trials=1)
    # Should be in (0, 1]
    assert 0.0 < dsr_1 <= 1.0


def test_dsr_n_trials_10_less_than_n_trials_1():
    """More trials → more selection bias → lower DSR."""
    observed_sr = 1.0
    sr_estimates = list(np.random.default_rng(7).normal(0.5, 0.3, 100))
    dsr_1 = deflated_sharpe_ratio(observed_sr, sr_estimates, n_trials=1)
    dsr_10 = deflated_sharpe_ratio(observed_sr, sr_estimates, n_trials=10)
    assert dsr_10 < dsr_1, f"Expected dsr_10 ({dsr_10:.4f}) < dsr_1 ({dsr_1:.4f})"


def test_dsr_returns_float_in_unit_interval():
    sr_estimates = [0.5, 0.6, 0.7, 0.8, 1.0]
    result = deflated_sharpe_ratio(1.2, sr_estimates, n_trials=5)
    assert 0.0 <= result <= 1.0


def test_dsr_empty_estimates_raises():
    with pytest.raises(ValueError, match="non-empty"):
        deflated_sharpe_ratio(1.0, [], n_trials=1)


def test_dsr_n_trials_1_approx_raw_sharpe_test():
    """For n_trials=1 with Gaussian returns, DSR ≈ Φ(SR * sqrt(T-1)).

    This verifies the n_trials=1 edge case: e_max=0, so DSR = Φ(SR/sqrt(Var_SR)).
    With skew=0, kurt=3: Var_SR = (1 - 0 + (3-1)/4 * SR²) / (T-1) = (1 + 0.5*SR²)/(T-1).
    """
    from scipy.stats import norm as scipy_norm

    observed_sr = 1.0
    T = 101  # len(sr_estimates)
    sr_estimates = [0.5] * T
    skew = 0.0
    kurtosis = 3.0

    dsr = deflated_sharpe_ratio(observed_sr, sr_estimates, n_trials=1, skew=skew, kurtosis=kurtosis)

    T_eff = T - 1
    var_sr = (1.0 - skew * observed_sr + (kurtosis - 1.0) / 4.0 * observed_sr ** 2) / T_eff
    z = (observed_sr - 0.0) / math.sqrt(var_sr)
    expected = float(scipy_norm.cdf(z))

    assert abs(dsr - expected) < 1e-9


# ---------------------------------------------------------------------------
# pr_auc_score
# ---------------------------------------------------------------------------

def test_pr_auc_perfect_classifier_equals_one():
    y_true = np.array([1, 1, 0, 0, 1, 0])
    # Perfect classifier: prob=1.0 for positives, 0.0 for negatives
    y_prob = np.where(y_true == 1, 1.0, 0.0)
    assert pr_auc_score(y_true, y_prob) == pytest.approx(1.0, abs=1e-9)


def test_pr_auc_random_classifier_approx_positive_rate():
    """Random classifier's PR-AUC ≈ positive rate (by definition of AP)."""
    rng = np.random.default_rng(123)
    n = 10_000
    positive_rate = 0.3
    y_true = (rng.uniform(size=n) < positive_rate).astype(int)
    y_prob = rng.uniform(size=n)  # uninformative scores
    auc = pr_auc_score(y_true, y_prob)
    # Allow generous tolerance since it's a statistical approximation
    assert abs(auc - positive_rate) < 0.05, f"Random PR-AUC {auc:.4f} far from {positive_rate}"


def test_pr_auc_in_unit_interval():
    rng = np.random.default_rng(0)
    y_true = rng.integers(0, 2, size=100)
    y_prob = rng.uniform(size=100)
    auc = pr_auc_score(y_true, y_prob)
    assert 0.0 <= auc <= 1.0


# ---------------------------------------------------------------------------
# max_drawdown
# ---------------------------------------------------------------------------

def test_max_drawdown_empty_series():
    assert max_drawdown(pd.Series([], dtype=float)) == 0.0


def test_max_drawdown_monotone_rising():
    equity = pd.Series([100.0, 110.0, 120.0, 130.0])
    assert max_drawdown(equity) == pytest.approx(0.0, abs=1e-9)


def test_max_drawdown_known_drawdown():
    # Peak 100, trough 80 → MDD = 20%
    equity = pd.Series([100.0, 90.0, 80.0, 85.0, 95.0])
    assert max_drawdown(equity) == pytest.approx(0.2, abs=1e-9)


# ---------------------------------------------------------------------------
# sharpe_improvement_ratio
# ---------------------------------------------------------------------------

def test_sharpe_improvement_ratio_gt_one_when_on_better():
    assert sharpe_improvement_ratio(1.5, 1.0) == pytest.approx(1.5, abs=1e-9)


def test_sharpe_improvement_ratio_zero_denominator():
    assert sharpe_improvement_ratio(1.0, 0.0) == 0.0
