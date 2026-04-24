"""Tests for src/backtest/cost.py — apply_cost helper."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from backtest.cost import (  # noqa: E402
    COST_CRYPTO_PER_SIDE,
    COST_KRX_BUY,
    COST_KRX_SELL,
    apply_cost,
)


def _make_series(values, index=None):
    if index is None:
        index = pd.date_range("2024-01-01", periods=len(values), freq="D")
    return pd.Series(values, index=index, dtype=float)


class TestApplyCostCrypto:
    def test_buy_entry_deducts_cost(self):
        """포지션 0→1 진입 시 편도 0.10% 차감."""
        returns = _make_series([0.0, 0.01])
        positions = _make_series([0.0, 1.0])
        net = apply_cost(returns, positions, "crypto")
        # bar 0: delta=0 (diff.fillna(0)), no cost
        assert net.iloc[0] == pytest.approx(0.0)
        # bar 1: delta=+1.0, buy cost = 0.001 * 1.0
        assert net.iloc[1] == pytest.approx(0.01 - COST_CRYPTO_PER_SIDE * 1.0)

    def test_sell_exit_deducts_cost(self):
        """포지션 1→0 청산 시 편도 0.10% 차감."""
        returns = _make_series([0.0, -0.005])
        positions = _make_series([1.0, 0.0])
        net = apply_cost(returns, positions, "crypto")
        # bar 1: delta=-1.0, sell cost = 0.001 * 1.0
        assert net.iloc[1] == pytest.approx(-0.005 - COST_CRYPTO_PER_SIDE * 1.0)

    def test_roundtrip_total_200bps(self):
        """매수+매도 왕복 비용 = 0.20%."""
        returns = _make_series([0.0, 0.0, 0.0])
        positions = _make_series([0.0, 1.0, 0.0])
        net = apply_cost(returns, positions, "crypto")
        total_cost = -(net.sum())
        assert total_cost == pytest.approx(COST_CRYPTO_PER_SIDE * 2, rel=1e-9)

    def test_no_change_no_cost(self):
        """포지션 변동 없으면 비용 0."""
        returns = _make_series([0.01, 0.02, -0.01])
        positions = _make_series([0.5, 0.5, 0.5])
        net = apply_cost(returns, positions, "crypto")
        # bar 0: diff=NaN→fillna(0), no cost
        pd.testing.assert_series_equal(net, returns)

    def test_multiple_turnovers_accumulate(self):
        """여러 번 전환 시 비용 누적 정확성."""
        returns = _make_series([0.0, 0.0, 0.0, 0.0, 0.0])
        positions = _make_series([0.0, 1.0, 0.0, 1.0, 0.0])
        net = apply_cost(returns, positions, "crypto")
        total_cost = -(net.sum())
        # 2 roundtrips = 4 half-turns
        assert total_cost == pytest.approx(COST_CRYPTO_PER_SIDE * 4, rel=1e-9)


class TestApplyCostKrx:
    def test_buy_entry_deducts_buy_cost(self):
        """KRX 매수 비용 0.015%."""
        returns = _make_series([0.0, 0.01])
        positions = _make_series([0.0, 1.0])
        net = apply_cost(returns, positions, "krx")
        assert net.iloc[1] == pytest.approx(0.01 - COST_KRX_BUY * 1.0)

    def test_sell_exit_deducts_sell_cost(self):
        """KRX 매도 비용 0.245% (거래세 포함)."""
        returns = _make_series([0.0, -0.005])
        positions = _make_series([1.0, 0.0])
        net = apply_cost(returns, positions, "krx")
        assert net.iloc[1] == pytest.approx(-0.005 - COST_KRX_SELL * 1.0)

    def test_asymmetric_costs(self):
        """KRX 비대칭: 매도 비용 > 매수 비용."""
        assert COST_KRX_SELL > COST_KRX_BUY

    def test_roundtrip_krx(self):
        """KRX 왕복 비용 = 매수 + 매도."""
        returns = _make_series([0.0, 0.0, 0.0])
        positions = _make_series([0.0, 1.0, 0.0])
        net = apply_cost(returns, positions, "krx")
        total_cost = -(net.sum())
        assert total_cost == pytest.approx(COST_KRX_BUY + COST_KRX_SELL, rel=1e-9)

    def test_krx_buy_015bps(self):
        """KRX 매수 비용 정확히 0.015%."""
        assert COST_KRX_BUY == pytest.approx(0.00015)

    def test_krx_sell_245bps(self):
        """KRX 매도 비용 정확히 0.245%."""
        assert COST_KRX_SELL == pytest.approx(0.00245)
