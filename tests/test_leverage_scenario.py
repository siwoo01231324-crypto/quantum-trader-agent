"""Regression tests for scripts/leverage_scenario.py — AC3."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from leverage_scenario import apply_leverage, compute_leverage_metrics


# ---------------------------------------------------------------------------
# apply_leverage tests
# ---------------------------------------------------------------------------

class TestApplyLeverage:
    def test_zero_returns_only_funding_drag(self):
        """Zero underlying returns → leveraged series = -(L-1)*daily_funding."""
        idx = pd.date_range("2024-01-01", periods=10, freq="D")
        returns = pd.Series(0.0, index=idx)
        lev = apply_leverage(returns, L=2.0, funding_rate_annual=0.073)
        daily_funding = 0.073 / 252
        expected = -(2.0 - 1) * daily_funding
        assert all(abs(v - expected) < 1e-12 for v in lev), (
            f"Expected {expected}, got {lev.values}"
        )

    def test_constant_returns_regression(self):
        """Constant 0.001/day with L=2 → known leveraged return each day."""
        idx = pd.date_range("2024-01-01", periods=20, freq="D")
        r = 0.001
        returns = pd.Series(r, index=idx)
        funding = 0.073
        daily_funding = funding / 252
        lev = apply_leverage(returns, L=2.0, funding_rate_annual=funding)
        expected = 2.0 * r - (2.0 - 1) * daily_funding
        assert all(abs(v - expected) < 1e-12 for v in lev), (
            f"Expected {expected:.8f} per day, got {lev.values}"
        )

    def test_ruin_clamped_to_minus_one(self):
        """L=5, daily return=-0.25 → leveraged=-1.25, clamped to -1.0."""
        idx = pd.date_range("2024-01-01", periods=5, freq="D")
        returns = pd.Series(-0.25, index=idx)
        lev = apply_leverage(returns, L=5.0, funding_rate_annual=0.073)
        assert all(v == -1.0 for v in lev), (
            f"Expected all -1.0 (ruin clamped), got {lev.values}"
        )

    def test_l1_no_leverage_no_funding(self):
        """L=1 with any funding → original returns unchanged (funding term = 0)."""
        idx = pd.date_range("2024-01-01", periods=10, freq="D")
        rng = np.random.default_rng(42)
        returns = pd.Series(rng.normal(0, 0.01, 10), index=idx)
        lev = apply_leverage(returns, L=1.0, funding_rate_annual=0.073)
        # (L-1) * daily_funding = 0, so leveraged == original
        pd.testing.assert_series_equal(lev, returns, check_names=False)


# ---------------------------------------------------------------------------
# compute_leverage_metrics tests
# ---------------------------------------------------------------------------

class TestComputeLeverageMetrics:
    def _make_returns(self, n: int = 252, daily_r: float = 0.001, seed: int = 0) -> pd.Series:
        idx = pd.date_range("2023-01-01", periods=n, freq="D")
        rng = np.random.default_rng(seed)
        noise = rng.normal(0, 0.005, n)
        return pd.Series(daily_r + noise, index=idx)

    def test_sharpe_invariant_pure_leverage_no_funding(self):
        """Sharpe(L=k) ≈ Sharpe(L=1) when funding=0 (pure leverage, no cost)."""
        returns = self._make_returns(n=756, daily_r=0.001, seed=99)
        m1 = compute_leverage_metrics(returns, L=1.0, funding=0.0)
        m3 = compute_leverage_metrics(returns, L=3.0, funding=0.0)
        m5 = compute_leverage_metrics(returns, L=5.0, funding=0.0)
        # Sharpe invariant under pure scaling
        assert abs(m1["sharpe"] - m3["sharpe"]) < 0.05, (
            f"Sharpe L=1 ({m1['sharpe']:.4f}) vs L=3 ({m3['sharpe']:.4f}) diverges"
        )
        assert abs(m1["sharpe"] - m5["sharpe"]) < 0.05, (
            f"Sharpe L=1 ({m1['sharpe']:.4f}) vs L=5 ({m5['sharpe']:.4f}) diverges"
        )

    def test_mdd_increases_with_leverage(self):
        """MDD(L=5) <= MDD(L=3) <= MDD(L=1) (MDD is negative, more leverage = worse)."""
        returns = self._make_returns(n=756, daily_r=0.001, seed=42)
        m1 = compute_leverage_metrics(returns, L=1.0, funding=0.073)
        m3 = compute_leverage_metrics(returns, L=3.0, funding=0.073)
        m5 = compute_leverage_metrics(returns, L=5.0, funding=0.073)
        assert m5["mdd"] <= m3["mdd"] <= m1["mdd"], (
            f"MDD order violated: L=1({m1['mdd']:.4f}) L=3({m3['mdd']:.4f}) L=5({m5['mdd']:.4f})"
        )

    def test_metrics_keys_present(self):
        """compute_leverage_metrics returns all required keys."""
        returns = self._make_returns(n=252)
        result = compute_leverage_metrics(returns, L=1.0, funding=0.073)
        required = {"L", "funding_annual", "annual_return", "sharpe", "mdd", "cvar_975", "monthly_10pct_hit_ratio"}
        assert required.issubset(result.keys()), f"Missing keys: {required - result.keys()}"

    def test_zero_returns_hit_ratio_zero(self):
        """With zero underlying returns and high funding, monthly hit ratio = 0."""
        idx = pd.date_range("2023-01-01", periods=756, freq="D")
        returns = pd.Series(0.0, index=idx)
        result = compute_leverage_metrics(returns, L=3.0, funding=0.073)
        assert result["monthly_10pct_hit_ratio"] == 0.0, (
            f"Expected 0.0 hit ratio, got {result['monthly_10pct_hit_ratio']}"
        )

    def test_annual_return_higher_with_leverage_positive_alpha(self):
        """Positive alpha: annual_return(L=3) > annual_return(L=1) with low funding."""
        returns = self._make_returns(n=756, daily_r=0.002, seed=7)
        m1 = compute_leverage_metrics(returns, L=1.0, funding=0.0)
        m3 = compute_leverage_metrics(returns, L=3.0, funding=0.0)
        assert m3["annual_return"] > m1["annual_return"], (
            f"Expected L=3 annual return > L=1: {m3['annual_return']:.4f} vs {m1['annual_return']:.4f}"
        )
