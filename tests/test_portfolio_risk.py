"""Tests for src/risk/portfolio.py + Policy.per_portfolio_risk integration.

Buckets (per 01_plan.md §C):
- Unit        : pure-function sanity on each metric
- Edge        : degenerate inputs (N=1, T<30, NaN, rank-deficient, float clip)
- Integration : Snapshot(portfolio_risk=...) + Policy(per_portfolio_risk=...) → Decision
- E2E         : df → compute_portfolio_risk_from_df → Snapshot → evaluate → Decision
- Observability: rule_id shape, warning category, message format (risk-rule-dsl.md §7)
- Precedence  : per_portfolio before per_portfolio_risk
- Benchmark   : evaluate() p99 < 100µs evidence gate (plan Amendment #7)
"""
from __future__ import annotations

import math
import sys
import timeit
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from risk.dsl import (  # noqa: E402
    Policy, PerPortfolio, PerPortfolioRisk, Snapshot, Order, Action, evaluate,
)
from risk.portfolio import (  # noqa: E402
    PortfolioRiskReport, ShortSampleWarning,
    shrinkage_covariance, historical_cvar,
    effective_number_of_bets, average_pairwise_correlation,
    compute_portfolio_risk_from_df,
)


# ---------- fixtures ----------

def _returns_np(T: int = 60, N: int = 3, rho: float = 0.0, seed: int = 42) -> np.ndarray:
    """T×N numpy returns with pairwise correlation rho."""
    rng = np.random.default_rng(seed)
    if N == 1:
        return rng.standard_normal(T).reshape(T, 1) * 0.01
    base = rng.standard_normal((T, 1))
    idio = rng.standard_normal((T, N))
    mat = rho * base + math.sqrt(max(0.0, 1.0 - rho ** 2)) * idio
    return mat * 0.01


def _returns_df(T: int = 60, N: int = 3, rho: float = 0.0, seed: int = 42) -> pd.DataFrame:
    arr = _returns_np(T=T, N=N, rho=rho, seed=seed)
    return pd.DataFrame(arr, columns=[f"s{i}" for i in range(N)])


def _report(
    cvar: float = 0.02, var: float = 0.015, corr: float = 0.2,
    enb: float = 2.7, enb_ratio: float = 0.9, N: int = 3, T: int = 60,
    alpha: float = 0.975,
) -> PortfolioRiskReport:
    return PortfolioRiskReport(
        cvar_pct=cvar, var_pct=var, corr_avg=corr, enb=enb, enb_ratio=enb_ratio,
        n_strategies=N, n_observations=T, alpha=alpha,
        ts=datetime(2026, 4, 24, tzinfo=timezone.utc),
    )


def _policy_with_risk(**overrides) -> Policy:
    ppr_kwargs = {"max_cvar_pct": 0.08, "max_corr_avg": 0.80, "min_enb_ratio": 0.5}
    ppr_kwargs.update(overrides)
    return Policy(
        policy_version=1, name="test",
        per_portfolio_risk=PerPortfolioRisk(**ppr_kwargs),
    )


def _snap_with_report(report: PortfolioRiskReport | None = None) -> Snapshot:
    return Snapshot(
        intent=Order(symbol="A", side="buy", qty=1, price=1_000),
        equity_krw=10_000_000,
        portfolio_risk=report,
    )


# ================================================================
# C.1 Unit — 13 tests
# ================================================================

def test_shrinkage_covariance_basic():
    cov = shrinkage_covariance(_returns_np(T=60, N=3, seed=1))
    assert cov.shape == (3, 3)
    assert np.allclose(cov, cov.T)
    assert np.all(np.diag(cov) > 0)
    assert float(np.linalg.eigvalsh(cov).min()) >= -1e-10


def test_shrinkage_covariance_numpy_only():
    """Core function accepts plain ndarray; pandas not required."""
    arr = _returns_np(T=60, N=3)
    cov = shrinkage_covariance(arr)
    assert isinstance(cov, np.ndarray)


def test_shrinkage_covariance_short_sample_warns():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cov = shrinkage_covariance(_returns_np(T=10, N=5))
    assert any(issubclass(x.category, ShortSampleWarning) for x in w)
    assert cov.shape == (5, 5)


def test_shrinkage_covariance_psd_guard():
    """Rank-deficient input: LW shrinkage keeps Σ PSD."""
    rng = np.random.default_rng(0)
    base = rng.standard_normal((40, 1))
    arr = np.hstack([base, base, base + 1e-9 * rng.standard_normal((40, 1))])
    cov = shrinkage_covariance(arr)
    eig_min = float(np.linalg.eigvalsh(cov).min())
    assert eig_min >= -1e-10


def test_shrinkage_covariance_dropna_via_wrapper():
    """Core rejects NaN; wrapper drops NaN rows → same core result."""
    arr = _returns_np(T=60, N=3)
    df = pd.DataFrame(arr)
    df.iloc[5] = np.nan
    report = compute_portfolio_risk_from_df(df)
    assert report.n_observations == 59


def test_historical_cvar_monotone():
    r = np.random.default_rng(1).standard_normal(1000) * 0.01
    assert historical_cvar(r, alpha=0.90) <= historical_cvar(r, alpha=0.975)


def test_historical_cvar_all_negative():
    r = -np.abs(np.random.default_rng(2).standard_normal(200)) * 0.01
    cvar = historical_cvar(r, alpha=0.975)
    assert cvar > 0


def test_historical_cvar_single_obs():
    assert historical_cvar(np.array([-0.05]), alpha=0.975) == pytest.approx(0.05)


def test_effective_number_of_bets_uncorrelated():
    cov = np.eye(4)
    w = np.full(4, 0.25)
    assert effective_number_of_bets(w, cov) == pytest.approx(4.0, rel=1e-6)


def test_effective_number_of_bets_perfectly_correlated():
    cov = np.ones((4, 4))
    w = np.full(4, 0.25)
    assert effective_number_of_bets(w, cov) == pytest.approx(1.0, rel=1e-6)


def test_effective_number_of_bets_degenerate_sigma():
    cov = np.zeros((3, 3))
    w = np.full(3, 1.0 / 3)
    assert math.isnan(effective_number_of_bets(w, cov))


def test_average_pairwise_correlation_clamped():
    cov = np.array([[1.0, 0.99999], [0.99999, 1.0]])
    rho = average_pairwise_correlation(cov)
    assert -1.0 <= rho <= 1.0
    assert rho == pytest.approx(0.99999, abs=1e-5)


def test_portfolio_risk_report_frozen_extra_forbid():
    r = _report()
    with pytest.raises(Exception):
        r.cvar_pct = 0.99  # frozen
    with pytest.raises(Exception):
        PortfolioRiskReport(
            cvar_pct=0.0, var_pct=0.0, corr_avg=0.0, enb=1.0, enb_ratio=1.0,
            n_strategies=1, n_observations=2, alpha=0.975,
            ts=datetime(2026, 4, 24, tzinfo=timezone.utc),
            unknown_field=1,  # extra=forbid
        )


# ================================================================
# C.5 Edge — 6 tests
# ================================================================

def test_n1_strategy_avg_corr_zero():
    cov = np.array([[0.001]])
    assert average_pairwise_correlation(cov) == 0.0


def test_t_less_than_n_warning():
    with pytest.warns(ShortSampleWarning):
        shrinkage_covariance(_returns_np(T=5, N=10))


def test_nan_column_raises_in_wrapper():
    df = _returns_df(T=60, N=3)
    df.iloc[:, 0] = np.nan
    with pytest.raises(ValueError):
        compute_portfolio_risk_from_df(df)


def test_zero_equity_still_evaluates():
    snap = Snapshot(
        intent=Order(symbol="A", side="buy", qty=1, price=1_000),
        equity_krw=0,
        portfolio_risk=_report(cvar=0.50),
    )
    policy = _policy_with_risk()
    d = evaluate(policy, snap)
    assert d.action == Action.REDUCE
    assert d.rule_id == "per_portfolio_risk.max_cvar_pct"


def test_rho_clamp_out_of_range():
    cov = np.array([[1.0, 1.0 + 1e-12], [1.0 + 1e-12, 1.0]])
    rho = average_pairwise_correlation(cov)
    assert -1.0 <= rho <= 1.0


def test_near_singular_sigma_enb_clamped_is_breach():
    """Degenerate Σ → wrapper clamps enb_ratio to 0.0 → evaluator treats as breach."""
    snap = _snap_with_report(_report(enb_ratio=0.0))
    policy = _policy_with_risk(min_enb_ratio=0.5)
    d = evaluate(policy, snap)
    assert d.action == Action.HALT
    assert d.rule_id == "per_portfolio_risk.min_enb_ratio"


# ================================================================
# C.2 Integration — 6 tests
# ================================================================

def test_evaluate_cvar_breach_reduces():
    d = evaluate(_policy_with_risk(max_cvar_pct=0.08),
                 _snap_with_report(_report(cvar=0.12)))
    assert d.action == Action.REDUCE
    assert d.rule_id == "per_portfolio_risk.max_cvar_pct"


def test_evaluate_cvar_allow():
    d = evaluate(_policy_with_risk(max_cvar_pct=0.99),
                 _snap_with_report(_report(cvar=0.02)))
    assert d.action == Action.ALLOW
    assert d.rule_id is None


def test_evaluate_corr_breach_blocks():
    d = evaluate(_policy_with_risk(max_corr_avg=0.80, max_cvar_pct=0.99),
                 _snap_with_report(_report(corr=0.95)))
    assert d.action == Action.BLOCK
    assert d.rule_id == "per_portfolio_risk.max_corr_avg"


def test_evaluate_enb_breach_halts():
    d = evaluate(_policy_with_risk(min_enb_ratio=0.5, max_cvar_pct=0.99, max_corr_avg=0.99),
                 _snap_with_report(_report(enb_ratio=0.3)))
    assert d.action == Action.HALT
    assert d.rule_id == "per_portfolio_risk.min_enb_ratio"


def test_evaluate_no_report_allows():
    """Regression guard: policy with rules + snapshot without report → ALLOW."""
    d = evaluate(_policy_with_risk(), _snap_with_report(None))
    assert d.action == Action.ALLOW


def test_evaluate_no_policy_block_allows():
    """Snapshot with report + policy WITHOUT per_portfolio_risk → ALLOW."""
    policy = Policy(policy_version=1, name="t")
    d = evaluate(policy, _snap_with_report(_report(cvar=0.99)))
    assert d.action == Action.ALLOW


# ================================================================
# C.6 Precedence — 1 test (per_portfolio before per_portfolio_risk)
# ================================================================

def test_precedence_per_portfolio_before_risk():
    """per_portfolio.max_leverage triggers before per_portfolio_risk.max_cvar_pct."""
    policy = Policy(
        policy_version=1, name="t",
        per_portfolio=PerPortfolio(max_leverage=1.0),
        per_portfolio_risk=PerPortfolioRisk(max_cvar_pct=0.01, on_cvar_breach=Action.REDUCE),
    )
    snap = Snapshot(
        intent=Order(symbol="A", side="buy", qty=100, price=10_000),
        equity_krw=1_000_000,
        gross_exposure_krw=2_000_000,
        portfolio_risk=_report(cvar=0.50),
    )
    d = evaluate(policy, snap)
    assert d.rule_id == "per_portfolio.max_leverage"


# ================================================================
# C.3 E2E — 3 tests (df → report → snapshot → evaluate)
# ================================================================

def test_e2e_cvar_path():
    df = _returns_df(T=200, N=3, rho=0.1, seed=11)
    report = compute_portfolio_risk_from_df(df)
    snap = _snap_with_report(report)
    policy = _policy_with_risk(max_cvar_pct=1e-6, max_corr_avg=0.99, min_enb_ratio=1e-6)
    d = evaluate(policy, snap)
    assert d.action == Action.REDUCE
    assert d.rule_id == "per_portfolio_risk.max_cvar_pct"


def test_e2e_corr_path():
    df = _returns_df(T=200, N=3, rho=0.97, seed=12)
    report = compute_portfolio_risk_from_df(df)
    assert report.corr_avg > 0.80
    snap = _snap_with_report(report)
    policy = _policy_with_risk(max_cvar_pct=1.0 - 1e-9, max_corr_avg=0.80, min_enb_ratio=1e-6)
    d = evaluate(policy, snap)
    assert d.action == Action.BLOCK
    assert d.rule_id == "per_portfolio_risk.max_corr_avg"


def test_e2e_enb_path():
    df = _returns_df(T=200, N=4, rho=0.98, seed=13)
    report = compute_portfolio_risk_from_df(df)
    assert report.enb_ratio < 0.5
    snap = _snap_with_report(report)
    policy = _policy_with_risk(max_cvar_pct=1.0 - 1e-9, max_corr_avg=0.999, min_enb_ratio=0.5)
    d = evaluate(policy, snap)
    assert d.action == Action.HALT
    assert d.rule_id == "per_portfolio_risk.min_enb_ratio"


# ================================================================
# C.4 Observability — 3 tests
# ================================================================

def test_breach_emits_rule_id_exactly():
    """Label space must be stable string literal (qta_risk_breach_total{rule_id=...})."""
    d = evaluate(_policy_with_risk(max_cvar_pct=0.01),
                 _snap_with_report(_report(cvar=0.10)))
    assert d.rule_id == "per_portfolio_risk.max_cvar_pct"


def test_short_sample_warning_category():
    with pytest.warns(ShortSampleWarning):
        shrinkage_covariance(_returns_np(T=10, N=5))


def test_decision_message_format_matches_spec():
    """risk-rule-dsl.md §7 audit-log format: '<metric> <value> > <threshold>'."""
    d = evaluate(_policy_with_risk(max_cvar_pct=0.05),
                 _snap_with_report(_report(cvar=0.10)))
    assert d.message is not None
    assert "cvar" in d.message
    assert ">" in d.message
    assert "0.10" in d.message
    assert "0.05" in d.message


# ================================================================
# Benchmark — Amendment #7 evidence gate
# ================================================================

def test_evaluate_latency_p99_under_100us():
    policy = _policy_with_risk()
    snap = _snap_with_report(_report())
    times = timeit.repeat(lambda: evaluate(policy, snap), repeat=50, number=100)
    per_call_us = sorted([t / 100 * 1e6 for t in times])
    p99 = per_call_us[int(0.99 * len(per_call_us)) - 1]
    assert p99 < 100, f"evaluate() p99={p99:.1f}µs > 100µs budget"
