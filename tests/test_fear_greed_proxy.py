"""Tests for P4 fear_greed_proxy — compute_fear_greed_proxy() + Snapshot.fear_greed_proxy +
PerPortfolioRisk.extreme_fear_block + evaluate() path.

Patent-avoidance note: 업리치 특허 R2 의 가격 컴포넌트만 단순 차용.
소셜 감성·거시경제 크롤링 요소 의도적 배제.
가격 기반 52주 rolling-max 비율만 사용.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from portfolio.orchestrator import compute_fear_greed_proxy, StrategyOrchestrator  # noqa: E402
from risk.dsl import (  # noqa: E402
    Action, Order, PerPortfolioRisk, Policy, Snapshot, evaluate,
)
from risk import PortfolioRiskReport  # noqa: E402


# ---------- helpers ----------

def _prices(n: int = 300, start: float = 100.0, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    returns = rng.standard_normal(n) * 0.01
    prices = start * np.cumprod(1 + returns)
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.Series(prices, index=idx)


def _snap(fear_greed_proxy=None) -> Snapshot:
    return Snapshot(
        intent=Order(symbol="A", side="buy", qty=1, price=1_000),
        equity_krw=10_000_000,
        fear_greed_proxy=fear_greed_proxy,
    )


def _report() -> PortfolioRiskReport:
    return PortfolioRiskReport(
        cvar_pct=0.02, var_pct=0.015, corr_avg=0.2,
        enb=2.7, enb_ratio=0.9, n_strategies=3, n_observations=60,
        alpha=0.975, ts=datetime(2026, 4, 24, tzinfo=timezone.utc),
    )


# ================================================================
# compute_fear_greed_proxy() — pure function tests
# ================================================================

def test_compute_fear_greed_proxy_range():
    """Result is always in [0, 1]."""
    prices = _prices(n=300)
    result = compute_fear_greed_proxy(prices)
    assert 0.0 <= result <= 1.0


def test_compute_fear_greed_proxy_peak():
    """Current price == rolling_max(252) → proxy == 1.0."""
    # Monotonically increasing prices: last price is always the 252d max
    prices = pd.Series(
        np.linspace(100, 200, 300),
        index=pd.date_range("2025-01-01", periods=300, freq="D"),
    )
    result = compute_fear_greed_proxy(prices, window=252)
    assert result == pytest.approx(1.0)


def test_compute_fear_greed_proxy_bottom():
    """Price at ~20% of rolling_max → proxy ≈ 0.2."""
    # 252 bars at 100, then one bar at 20
    highs = [100.0] * 252
    lows = [20.0]
    prices = pd.Series(
        highs + lows,
        index=pd.date_range("2025-01-01", periods=253, freq="D"),
    )
    result = compute_fear_greed_proxy(prices, window=252)
    assert result == pytest.approx(0.2, abs=0.01)


def test_compute_fear_greed_proxy_short_series_uses_available():
    """Series shorter than window still returns a valid [0,1] value."""
    prices = _prices(n=50)
    result = compute_fear_greed_proxy(prices, window=252)
    assert 0.0 <= result <= 1.0


def test_compute_fear_greed_proxy_constant_prices():
    """All-same prices → proxy == 1.0 (current == rolling_max)."""
    prices = pd.Series(
        [100.0] * 100,
        index=pd.date_range("2025-01-01", periods=100, freq="D"),
    )
    result = compute_fear_greed_proxy(prices)
    assert result == pytest.approx(1.0)


# ================================================================
# Snapshot.fear_greed_proxy — optional field validation
# ================================================================

def test_snapshot_fear_greed_proxy_optional_none():
    """Snapshot can be constructed without fear_greed_proxy (default None)."""
    snap = _snap()
    assert snap.fear_greed_proxy is None


def test_snapshot_fear_greed_proxy_float_preserved():
    """Valid float in [0,1] is accepted and preserved."""
    snap = _snap(fear_greed_proxy=0.35)
    assert snap.fear_greed_proxy == pytest.approx(0.35)


def test_snapshot_fear_greed_proxy_boundary_values():
    """Boundary values 0.0 and 1.0 are accepted."""
    assert _snap(fear_greed_proxy=0.0).fear_greed_proxy == pytest.approx(0.0)
    assert _snap(fear_greed_proxy=1.0).fear_greed_proxy == pytest.approx(1.0)


def test_snapshot_fear_greed_proxy_out_of_range_raises():
    """Values outside [0,1] raise ValidationError."""
    with pytest.raises((ValidationError, Exception)):
        _snap(fear_greed_proxy=1.1)
    with pytest.raises((ValidationError, Exception)):
        _snap(fear_greed_proxy=-0.01)


# ================================================================
# PerPortfolioRisk.extreme_fear_block + evaluate() path
# ================================================================

def test_per_portfolio_risk_extreme_fear_block_default_none():
    """extreme_fear_block defaults to None (disabled)."""
    ppr = PerPortfolioRisk()
    assert ppr.extreme_fear_block is None


def test_per_portfolio_risk_extreme_fear_threshold_default():
    """extreme_fear_threshold default is 0.2."""
    ppr = PerPortfolioRisk(extreme_fear_block=True)
    assert ppr.extreme_fear_threshold == pytest.approx(0.2)


def test_extreme_fear_block_dsl_buy_blocked():
    """extreme_fear_block=True + fear_greed_proxy < 0.2 + buy side → BLOCK."""
    policy = Policy(
        policy_version=1, name="test",
        per_portfolio_risk=PerPortfolioRisk(
            extreme_fear_block=True,
            extreme_fear_threshold=0.2,
        ),
    )
    snap = _snap(fear_greed_proxy=0.1)
    d = evaluate(policy, snap)
    assert d.action == Action.BLOCK
    assert d.rule_id == "per_portfolio_risk.extreme_fear_block"


def test_extreme_fear_block_dsl_sell_not_blocked():
    """extreme_fear_block only blocks buy orders; sell is allowed."""
    policy = Policy(
        policy_version=1, name="test",
        per_portfolio_risk=PerPortfolioRisk(
            extreme_fear_block=True,
            extreme_fear_threshold=0.2,
        ),
    )
    snap = Snapshot(
        intent=Order(symbol="A", side="sell", qty=1, price=1_000),
        equity_krw=10_000_000,
        fear_greed_proxy=0.05,
    )
    d = evaluate(policy, snap)
    assert d.action == Action.ALLOW


def test_extreme_fear_block_dsl_proxy_above_threshold_allows():
    """fear_greed_proxy >= threshold → no block."""
    policy = Policy(
        policy_version=1, name="test",
        per_portfolio_risk=PerPortfolioRisk(
            extreme_fear_block=True,
            extreme_fear_threshold=0.2,
        ),
    )
    snap = _snap(fear_greed_proxy=0.25)
    d = evaluate(policy, snap)
    assert d.action == Action.ALLOW


def test_extreme_fear_block_disabled_by_default():
    """No extreme_fear_block in policy → no block even with very low proxy."""
    policy = Policy(
        policy_version=1, name="test",
        per_portfolio_risk=PerPortfolioRisk(),
    )
    snap = _snap(fear_greed_proxy=0.0)
    d = evaluate(policy, snap)
    assert d.action == Action.ALLOW


def test_extreme_fear_block_no_proxy_no_block():
    """extreme_fear_block=True but snap.fear_greed_proxy=None → no block."""
    policy = Policy(
        policy_version=1, name="test",
        per_portfolio_risk=PerPortfolioRisk(extreme_fear_block=True),
    )
    snap = _snap(fear_greed_proxy=None)
    d = evaluate(policy, snap)
    assert d.action == Action.ALLOW


def test_extreme_fear_block_custom_threshold():
    """Custom extreme_fear_threshold respected."""
    policy = Policy(
        policy_version=1, name="test",
        per_portfolio_risk=PerPortfolioRisk(
            extreme_fear_block=True,
            extreme_fear_threshold=0.5,
        ),
    )
    # proxy=0.3 < 0.5 → BLOCK
    snap = _snap(fear_greed_proxy=0.3)
    d = evaluate(policy, snap)
    assert d.action == Action.BLOCK

    # proxy=0.6 >= 0.5 → ALLOW
    snap2 = _snap(fear_greed_proxy=0.6)
    d2 = evaluate(policy, snap2)
    assert d2.action == Action.ALLOW


# ================================================================
# Orchestrator wiring test
# ================================================================

def _daily(returns: list[float], start: str = "2026-04-01") -> pd.Series:
    idx = pd.date_range(start=start, periods=len(returns), freq="D")
    return pd.Series(returns, index=idx, dtype=float)


def test_orchestrator_compute_fear_greed_proxy_wiring():
    """evaluate_order() accepts fear_greed_proxy via snap_extras and evaluates correctly."""
    policy = Policy(
        policy_version=1, name="test",
        per_portfolio_risk=PerPortfolioRisk(
            extreme_fear_block=True,
            extreme_fear_threshold=0.2,
        ),
    )
    orch = StrategyOrchestrator(policy)
    order = Order(symbol="BTCUSDT", side="buy", qty=0.01, price=50_000)

    # With low fear_greed_proxy → BLOCK
    d = orch.evaluate_order(order, equity_krw=10_000_000, fear_greed_proxy=0.1)
    assert d.action == Action.BLOCK
    assert d.rule_id == "per_portfolio_risk.extreme_fear_block"

    # With high fear_greed_proxy → ALLOW
    d2 = orch.evaluate_order(order, equity_krw=10_000_000, fear_greed_proxy=0.9)
    assert d2.action == Action.ALLOW
