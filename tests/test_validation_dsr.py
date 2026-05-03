"""Tests for src/ml/validation/deflated_sharpe.py."""
from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.stats import norm

from src.ml.validation import (
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
)


def test_psr_closed_form() -> None:
    """PSR with skew=0, excess kurt=0 reduces to Phi(SR * sqrt(n-1))."""
    sr = 2.0
    n = 252
    psr = probabilistic_sharpe_ratio(
        observed_sr=sr, sr_benchmark=0.0, n_obs=n, skew=0.0, kurtosis_excess=0.0
    )
    expected = float(norm.cdf(sr * math.sqrt(n - 1)))
    assert math.isclose(psr, expected, abs_tol=1e-8)


def test_psr_at_benchmark_is_half() -> None:
    """SR exactly at benchmark gives PSR = 0.5."""
    psr = probabilistic_sharpe_ratio(
        observed_sr=1.0, sr_benchmark=1.0, n_obs=252, skew=0.0, kurtosis_excess=0.0
    )
    assert math.isclose(psr, 0.5, abs_tol=1e-12)


def test_psr_below_benchmark_is_low() -> None:
    """SR below benchmark gives PSR < 0.5."""
    psr = probabilistic_sharpe_ratio(
        observed_sr=0.0, sr_benchmark=1.0, n_obs=252, skew=0.0, kurtosis_excess=0.0
    )
    assert psr < 0.5


def test_dsr_monotonicity_in_n_trials() -> None:
    """For fixed observed SR and SR estimate variance, DSR is non-increasing in N."""
    rng = np.random.default_rng(42)
    sr_estimates = rng.normal(loc=0.5, scale=0.5, size=64)
    observed_sr = float(sr_estimates.max()) + 0.5  # well above the maximum

    prev = 1.0
    for n in (1, 2, 4, 8, 16, 32, 64):
        dsr = deflated_sharpe_ratio(
            observed_sr=observed_sr,
            sr_estimates=sr_estimates[:n],
            n_obs=252,
            skew=0.0,
            kurtosis_excess=0.0,
            n_trials=n,
        )
        assert 0.0 <= dsr <= 1.0
        # Allow tiny numerical wobble at single-trial boundary
        assert dsr <= prev + 1e-9
        prev = dsr


def test_dsr_boundary_n1_equals_psr() -> None:
    """N=1 must equal PSR with benchmark 0."""
    psr = probabilistic_sharpe_ratio(
        observed_sr=1.5, sr_benchmark=0.0, n_obs=252, skew=0.0, kurtosis_excess=0.0
    )
    dsr = deflated_sharpe_ratio(
        observed_sr=1.5,
        sr_estimates=np.array([1.5]),
        n_obs=252,
        skew=0.0,
        kurtosis_excess=0.0,
    )
    assert math.isclose(dsr, psr, abs_tol=1e-12)


def test_dsr_all_zero_sr() -> None:
    """All trials produce SR=0 (Var=0); DSR collapses to PSR(SR_benchmark=0)."""
    sr_estimates = np.zeros(8)
    dsr = deflated_sharpe_ratio(
        observed_sr=0.0,
        sr_estimates=sr_estimates,
        n_obs=252,
        skew=0.0,
        kurtosis_excess=0.0,
    )
    assert math.isclose(dsr, 0.5, abs_tol=1e-12)


def test_psr_skew_kurtosis_effect() -> None:
    """Negative skew or high excess kurtosis should reduce PSR.

    Use a smaller SR and n_obs so the PSR is not saturated at 1.0.
    """
    sr = 0.3
    n = 50
    base = probabilistic_sharpe_ratio(
        observed_sr=sr, sr_benchmark=0.0, n_obs=n, skew=0.0, kurtosis_excess=0.0
    )
    neg_skew = probabilistic_sharpe_ratio(
        observed_sr=sr, sr_benchmark=0.0, n_obs=n, skew=-1.0, kurtosis_excess=0.0
    )
    high_kurt = probabilistic_sharpe_ratio(
        observed_sr=sr, sr_benchmark=0.0, n_obs=n, skew=0.0, kurtosis_excess=5.0
    )
    # Sanity: base must be away from saturation so the assertions are meaningful.
    assert 0.5 < base < 0.999
    assert neg_skew < base
    assert high_kurt < base


def test_psr_invalid_n_obs() -> None:
    with pytest.raises(ValueError):
        probabilistic_sharpe_ratio(1.0, 0.0, 1, 0.0, 0.0)


def test_dsr_empty_estimates() -> None:
    with pytest.raises(ValueError):
        deflated_sharpe_ratio(
            observed_sr=1.0,
            sr_estimates=np.array([]),
            n_obs=252,
            skew=0.0,
            kurtosis_excess=0.0,
        )
