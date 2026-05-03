"""Tests for compute_effective_n and judge_hypothesis n_eff trigger."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ml.reporting.cross_asset_compare import (
    CrossAssetReport,
    compute_effective_n,
    judge_hypothesis,
)


def _make_report(
    asset_id: str = "krx-pool-30",
    strategy_id: str = "momo-kis-v1-pooled",
    dsr_delta: float = 0.35,
    n_events: int = 500,
    n_symbols: int = 1,
    n_eff: float = 0.0,
    rho_avg: float = 0.0,
) -> CrossAssetReport:
    return CrossAssetReport(
        asset_id=asset_id,
        strategy_id=strategy_id,
        sr_off=0.5,
        sr_on=0.9,
        sr_delta=0.4,
        mdd_off=0.1,
        mdd_on=0.08,
        mdd_delta=-0.02,
        pr_auc=0.65,
        dsr_off=0.4,
        dsr_on=0.75,
        dsr_delta=dsr_delta,
        n_events=n_events,
        n_trials=1,
        data_window="2026-03 ~ 2026-04",
        periods_per_year=98280,
        n_symbols=n_symbols,
        n_eff=n_eff,
        rho_avg=rho_avg,
    )


class TestComputeEffectiveN:
    def test_single_symbol_returns_one(self):
        assert compute_effective_n(1, 0.0) == 1.0

    def test_zero_rho_returns_pool_size(self):
        assert compute_effective_n(10, 0.0) == 10.0

    def test_full_correlation_returns_approximately_one(self):
        result = compute_effective_n(30, 1.0)
        assert abs(result - 1.0) < 1e-9

    def test_partial_correlation(self):
        # N=30, rho=0.4 → 30 / (1 + 29*0.4) = 30 / 12.6 ≈ 2.381
        result = compute_effective_n(30, 0.4)
        assert abs(result - 30 / (1 + 29 * 0.4)) < 0.01

    def test_pool_size_le_1_always_returns_one(self):
        assert compute_effective_n(0, 0.5) == 1.0
        assert compute_effective_n(-1, 0.5) == 1.0

    def test_large_pool_zero_rho(self):
        assert compute_effective_n(100, 0.0) == 100.0


class TestJudgeHypothesisNEffTrigger:
    def test_n_eff_too_low_triggers_pending(self):
        """n_symbols=30, n_eff=2.4 → 보류 (n_eff < 5)."""
        report = _make_report(
            n_symbols=30,
            n_eff=2.4,
            rho_avg=0.4,
            n_events=500,
        )
        result = judge_hypothesis([report], dsr_threshold=0.3)
        assert result["verdict"] == "보류"
        assert "n_eff" in result["reason"]
        assert "2.4" in result["reason"] or "2.40" in result["reason"]

    def test_n_eff_exactly_5_does_not_trigger(self):
        """n_eff == 5 should NOT trigger the n_eff 보류 path."""
        report = _make_report(
            n_symbols=30,
            n_eff=5.0,
            rho_avg=0.2,
            n_events=500,
            dsr_delta=0.35,
        )
        result = judge_hypothesis([report], dsr_threshold=0.3)
        assert result["verdict"] != "보류" or "n_eff" not in result["reason"]

    def test_single_symbol_n_eff_zero_not_triggered(self):
        """n_symbols=1 skips n_eff check even if n_eff=0."""
        report = _make_report(
            n_symbols=1,
            n_eff=0.0,
            n_events=500,
            dsr_delta=0.35,
        )
        result = judge_hypothesis([report], dsr_threshold=0.3)
        assert result["verdict"] == "채택"

    def test_multi_symbol_sufficient_n_eff_proceeds(self):
        """n_symbols=10, n_eff=6.0 → should proceed to DSR judgment."""
        report = _make_report(
            n_symbols=10,
            n_eff=6.0,
            rho_avg=0.1,
            n_events=500,
            dsr_delta=0.35,
        )
        result = judge_hypothesis([report], dsr_threshold=0.3)
        assert result["verdict"] == "채택"
