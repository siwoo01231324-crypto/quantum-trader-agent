"""Extended tests for StrategyOrchestrator — reliability_score (issue #76)."""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from portfolio import StrategyOrchestrator  # noqa: E402
from risk import Policy, PerPortfolioRisk, Order  # noqa: E402


def _daily(returns, start="2024-01-01"):
    idx = pd.date_range(start=start, periods=len(returns), freq="D")
    return pd.Series(returns, index=idx, dtype=float)


def _policy():
    return Policy(
        policy_version=1, name="t",
        per_portfolio_risk=PerPortfolioRisk(max_cvar_pct=0.99, max_corr_avg=0.9, min_enb_ratio=0.1),
    )


# ---------------------------------------------------------------------------
# reliability_score: basic range checks
# ---------------------------------------------------------------------------

def test_reliability_score_20d_returns_in_unit_interval():
    """T=20 returns a score in [0, 1]."""
    rng = np.random.default_rng(1)
    orch = StrategyOrchestrator(_policy())
    orch.register_strategy_returns("s", _daily((rng.standard_normal(20) * 0.01).tolist()))
    score = orch.strategy_reliability_score("s")
    assert 0.0 <= score <= 1.0


def test_reliability_score_250d_returns_in_unit_interval():
    """T=250 (≥252-day threshold) returns a score in [0, 1]."""
    rng = np.random.default_rng(2)
    orch = StrategyOrchestrator(_policy())
    orch.register_strategy_returns("s", _daily((rng.standard_normal(250) * 0.01).tolist()))
    score = orch.strategy_reliability_score("s")
    assert 0.0 <= score <= 1.0


def test_reliability_score_unknown_strategy_returns_zero():
    """Unregistered strategy_id must return 0.0."""
    orch = StrategyOrchestrator(_policy())
    assert orch.strategy_reliability_score("nonexistent") == 0.0


# ---------------------------------------------------------------------------
# NaN guard: T < 20 → 0.0 not NaN
# ---------------------------------------------------------------------------

def test_reliability_nan_guard_at_T_lt_20():
    """T=19 must return exactly 0.0, not NaN (NaN guard per spec §2.3)."""
    rng = np.random.default_rng(3)
    orch = StrategyOrchestrator(_policy())
    orch.register_strategy_returns("s", _daily((rng.standard_normal(19) * 0.01).tolist()))
    score = orch.strategy_reliability_score("s")
    assert score == 0.0
    assert not math.isnan(score)


def test_reliability_nan_guard_at_T_eq_1():
    """T=1 must return 0.0, not NaN."""
    orch = StrategyOrchestrator(_policy())
    orch.register_strategy_returns("s", _daily([0.01]))
    score = orch.strategy_reliability_score("s")
    assert score == 0.0
    assert not math.isnan(score)


# ---------------------------------------------------------------------------
# Multiplicative structure verification
# ---------------------------------------------------------------------------

def test_reliability_drawdown_gate_zero_at_dd_20pct():
    """Max drawdown >= 20% must yield reliability = 0.0 (hard-zero gate)."""
    # Craft a series that loses >20%: steady decline
    returns = [-0.01] * 25  # cumulative: ~22% loss over 25 bars
    orch = StrategyOrchestrator(_policy())
    orch.register_strategy_returns("s", _daily(returns))
    score = orch.strategy_reliability_score("s")
    assert score == 0.0


def test_reliability_multiplicative_structure():
    """With high DD, score must be 0 regardless of convex_base value."""
    rng = np.random.default_rng(9)
    # Strategy A: good returns but massive drawdown
    good_then_crash = [0.02] * 30 + [-0.015] * 20  # crashes >20%
    orch = StrategyOrchestrator(_policy())
    orch.register_strategy_returns("crash", _daily(good_then_crash))
    score = orch.strategy_reliability_score("crash")
    assert score == 0.0


# ---------------------------------------------------------------------------
# drawdown_gate = 0 edge
# ---------------------------------------------------------------------------

def test_reliability_drawdown_gate_zero_edge():
    """DD >= 20% yields gate = 0.0 → reliability = 0.0."""
    # Build a series that clearly exceeds 20% drawdown:
    # 25 bars up +0.1% each, then 5 bars of -5% = ~25% total drop
    returns = [0.001] * 25 + [-0.05] * 5
    orch = StrategyOrchestrator(_policy())
    orch.register_strategy_returns("edge", _daily(returns))
    score = orch.strategy_reliability_score("edge")
    # With >20% drawdown, gate = 0 → score must be 0.0
    assert score == pytest.approx(0.0, abs=1e-6)
