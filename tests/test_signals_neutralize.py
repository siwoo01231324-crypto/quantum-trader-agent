"""Tests for signals.neutralize — OLS and Gram-Schmidt orthogonal methods."""
from __future__ import annotations

import numpy as np
import pytest


@pytest.mark.parametrize("method", ["ols", "orthogonal"])
def test_neutralize_known_answer_designed_collinearity(method):
    """Known-answer test with designed collinearity.

    exposure_a = [1,1,1,1], exposure_b = 2*exposure_a + eps~N(0,1e-8).
    raw = exposure_a * 3 + residual_signal.
    After neutralization, result should be ~residual_signal (orthogonal to both).
    """
    rng = np.random.default_rng(42)
    T = 100
    exposure_a = np.ones(T)
    eps = rng.normal(0, 1e-8, T)
    exposure_b = 2.0 * exposure_a + eps

    residual_signal = rng.standard_normal(T) * 0.1
    raw = 3.0 * exposure_a + residual_signal

    from signals.neutralize import neutralize

    result = neutralize(raw, exposure_a, exposure_b, method=method)

    assert result.shape == (T,)
    # Result must be orthogonal to exposure_a
    assert abs(np.dot(result, exposure_a)) < 1e-6, f"Not orthogonal to exposure_a: {np.dot(result, exposure_a)}"
    # Residual should be close to residual_signal (up to scale)
    corr = np.corrcoef(result, residual_signal)[0, 1]
    assert abs(corr) > 0.9, f"Correlation with true residual too low: {corr}"


@pytest.mark.parametrize("method", ["ols", "orthogonal"])
def test_neutralize_orthogonal_to_exposure(method):
    """Result must be orthogonal to each exposure vector."""
    rng = np.random.default_rng(7)
    T = 80
    exposure_a = rng.standard_normal(T)
    exposure_b = rng.standard_normal(T)
    raw = rng.standard_normal(T)

    from signals.neutralize import neutralize

    result = neutralize(raw, exposure_a, exposure_b, method=method)
    assert abs(np.dot(result, exposure_a) / T) < 1e-9
    assert abs(np.dot(result, exposure_b) / T) < 1e-9


def test_neutralize_gram_schmidt_idempotence():
    """Applying orthogonal neutralization twice yields same result as once."""
    rng = np.random.default_rng(13)
    T = 60
    exposure = rng.standard_normal(T)
    raw = rng.standard_normal(T)

    from signals.neutralize import neutralize

    once = neutralize(raw, exposure, method="orthogonal")
    twice = neutralize(once, exposure, method="orthogonal")
    np.testing.assert_allclose(once, twice, atol=1e-10)


def test_neutralize_degenerate_cond_fallback():
    """cond > 1e10 triggers fallback to OLS without raising."""
    T = 50
    exposure_a = np.ones(T)
    exposure_b = np.ones(T)  # exactly degenerate (cond = inf)
    raw = np.random.default_rng(99).standard_normal(T)

    from signals.neutralize import neutralize

    # Should not raise — fallback to OLS
    result = neutralize(raw, exposure_a, exposure_b, method="orthogonal")
    assert result.shape == (T,)
    assert not np.any(np.isnan(result))


def test_neutralize_no_exposures_returns_copy():
    """With zero exposures, raw is returned unchanged."""
    raw = np.array([1.0, 2.0, 3.0])
    from signals.neutralize import neutralize

    result = neutralize(raw)
    np.testing.assert_array_equal(result, raw)
    assert result is not raw  # must be a copy


def test_neutralize_synthetic_rankic_sanity():
    """After OLS neutralization, rank IC with exposure drops near zero."""
    rng = np.random.default_rng(55)
    T = 200
    exposure = rng.standard_normal(T)
    noise = rng.standard_normal(T) * 0.01
    raw = exposure + noise  # raw is essentially equal to exposure

    from signals.neutralize import neutralize
    from scipy.stats import spearmanr

    result = neutralize(raw, exposure, method="ols")
    ic, _ = spearmanr(result, exposure)
    assert abs(ic) < 0.05, f"Rank IC after neutralization too high: {ic}"
