"""Tests for StrategyOrchestrator stub (issue #70, extended scope).

Smoke coverage only — the full async orchestrator is issue #78.
Validates that the interface contract holds and that risk.evaluate() is
actually invoked end-to-end from strategy-return registration.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from portfolio import StrategyOrchestrator  # noqa: E402
from risk import (  # noqa: E402
    Policy, PerPortfolioRisk, Order, Action,
)


def _daily(returns: list[float], start: str = "2026-04-01") -> pd.Series:
    idx = pd.date_range(start=start, periods=len(returns), freq="D")
    return pd.Series(returns, index=idx, dtype=float)


def _policy_block_highcorr() -> Policy:
    return Policy(
        policy_version=1, name="t",
        per_portfolio_risk=PerPortfolioRisk(
            max_cvar_pct=0.99, max_corr_avg=0.50, min_enb_ratio=0.1,
        ),
    )


def _order() -> Order:
    return Order(symbol="BTCUSDT", side="buy", qty=0.01, price=50_000)


def test_stub_single_strategy_allows():
    """N=1 전략 → 리포트 미생성 → ALLOW (회귀 zero)."""
    orch = StrategyOrchestrator(_policy_block_highcorr())
    orch.register_strategy_returns("momo", _daily([0.01] * 60))
    report = orch.refresh_portfolio_risk(ts=datetime(2026, 4, 24, tzinfo=timezone.utc))
    assert report is None
    assert orch.current_report is None
    d = orch.evaluate_order(_order(), equity_krw=10_000_000)
    assert d.action == Action.ALLOW


def test_stub_two_correlated_strategies_trigger_corr_breach():
    """같이 움직이는 2전략 → ρ̄ > 0.5 → Decision(BLOCK, max_corr_avg)."""
    rng = np.random.default_rng(7)
    base = rng.standard_normal(90) * 0.01
    s_a = _daily(base.tolist())
    s_b = _daily((base + rng.standard_normal(90) * 0.001).tolist())  # 거의 복제
    orch = StrategyOrchestrator(_policy_block_highcorr())
    orch.register_strategy_returns("alpha_a", s_a)
    orch.register_strategy_returns("alpha_b", s_b)
    report = orch.refresh_portfolio_risk(ts=datetime(2026, 4, 24, tzinfo=timezone.utc))
    assert report is not None
    assert report.n_strategies == 2
    assert report.corr_avg > 0.5
    d = orch.evaluate_order(_order(), equity_krw=10_000_000)
    assert d.action == Action.BLOCK
    assert d.rule_id == "per_portfolio_risk.max_corr_avg"


def test_stub_independent_strategies_allow():
    """독립 2전략 → ρ̄ ≈ 0 → ALLOW."""
    rng = np.random.default_rng(11)
    s_a = _daily((rng.standard_normal(90) * 0.01).tolist())
    s_b = _daily((rng.standard_normal(90) * 0.01).tolist())
    orch = StrategyOrchestrator(_policy_block_highcorr())
    orch.register_strategy_returns("alpha_a", s_a)
    orch.register_strategy_returns("alpha_b", s_b)
    report = orch.refresh_portfolio_risk()
    assert report is not None
    assert abs(report.corr_avg) < 0.3
    d = orch.evaluate_order(_order(), equity_krw=10_000_000)
    assert d.action == Action.ALLOW


def test_stub_interface_contract():
    """공개 API signature 고정 (향후 #78 확장 시 하위호환 근거)."""
    orch = StrategyOrchestrator(_policy_block_highcorr())
    assert orch.registered_strategies() == []
    with pytest.raises(TypeError):
        orch.register_strategy_returns("x", [0.01, 0.02])  # pd.Series 아님
    orch.register_strategy_returns("x", _daily([0.01, 0.02, -0.01]))
    assert orch.registered_strategies() == ["x"]
