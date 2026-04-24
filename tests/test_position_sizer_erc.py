"""Tests for ERC convex approximation (P6 — patent avoidance)."""
import numpy as np
import pytest
from src.risk.position_sizer import equal_risk_contribution_convex


def _make_psd(n: int, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    A = rng.standard_normal((n, n))
    return A @ A.T + np.eye(n) * 0.1


def test_erc_convex_two_asset():
    """2-asset diagonal cov with different variances → weights ∝ 1/std."""
    cov = np.diag([1.0, 4.0])
    w = equal_risk_contribution_convex(cov)
    # ERC: w1*sigma1 == w2*sigma2 → w1/w2 == sigma2/sigma1 == 2
    assert abs(w[0] / w[1] - 2.0) < 0.01


def test_erc_convex_sums_to_one():
    cov = _make_psd(5)
    w = equal_risk_contribution_convex(cov)
    assert abs(w.sum() - 1.0) < 1e-6


def test_erc_convex_nonnegative():
    cov = _make_psd(10)
    w = equal_risk_contribution_convex(cov)
    assert np.all(w >= -1e-9)


def test_erc_convex_equal_risk_contrib():
    """w_i * (Σw)_i should be approximately equal for all i (identity cov)."""
    n = 3
    cov = np.eye(n)
    w = equal_risk_contribution_convex(cov)
    risk_contribs = w * (cov @ w)
    target = risk_contribs.mean()
    assert np.allclose(risk_contribs, target, atol=1e-4)


def test_erc_convex_50_asset_stress():
    """N=50 random PSD cov should converge without error."""
    cov = _make_psd(50)
    w = equal_risk_contribution_convex(cov)
    assert abs(w.sum() - 1.0) < 1e-6
    assert np.all(w >= -1e-9)


def test_erc_convex_high_condition_number():
    """Ill-conditioned cov (cond~1e8) triggers IVP fallback gracefully."""
    n = 5
    # Build a near-singular cov with one very small eigenvalue
    cov = np.eye(n, dtype=float)
    cov[0, 0] = 1e-8  # one tiny variance → condition number ~1e8
    w = equal_risk_contribution_convex(cov)
    # IVP fallback: result is still valid
    assert abs(w.sum() - 1.0) < 1e-6
    assert np.all(w >= -1e-9)
