"""Unit tests for src.backtest.cost (#132 coverage 0% → 95%+)."""
from __future__ import annotations

import pandas as pd
import pytest
from pandas.testing import assert_series_equal

from src.backtest.cost import (
    apply_cost,
    COST_CRYPTO_PER_SIDE,
    COST_KRX_BUY,
    COST_KRX_SELL,
)


# ---------------------------------------------------------------------------
# crypto: 양방향 0.10%
# ---------------------------------------------------------------------------

def test_crypto_buy_subtracts_per_side_cost():
    returns = pd.Series([0.0, 0.0, 0.0])
    positions = pd.Series([0.0, 1.0, 1.0])  # 0→1 매수
    out = apply_cost(returns, positions, instrument_type="crypto")
    # delta = [NaN→0, 1, 0]; buy 만 1 → 0.001 차감
    assert out.iloc[0] == pytest.approx(0.0)
    assert out.iloc[1] == pytest.approx(0.0 - COST_CRYPTO_PER_SIDE)
    assert out.iloc[2] == pytest.approx(0.0)


def test_crypto_sell_subtracts_per_side_cost():
    returns = pd.Series([0.0, 0.0, 0.0])
    positions = pd.Series([1.0, 1.0, 0.0])  # 1→0 매도
    out = apply_cost(returns, positions, instrument_type="crypto")
    assert out.iloc[2] == pytest.approx(-COST_CRYPTO_PER_SIDE)


def test_crypto_no_change_no_cost():
    returns = pd.Series([0.01, 0.02, -0.01])
    positions = pd.Series([1.0, 1.0, 1.0])  # delta = 0
    out = apply_cost(returns, positions, instrument_type="crypto")
    assert_series_equal(out, returns)


def test_crypto_partial_position_change():
    returns = pd.Series([0.0, 0.0])
    positions = pd.Series([0.0, 0.5])  # 50% 매수
    out = apply_cost(returns, positions, instrument_type="crypto")
    assert out.iloc[1] == pytest.approx(-COST_CRYPTO_PER_SIDE * 0.5)


def test_crypto_buy_and_sell_in_sequence():
    returns = pd.Series([0.0, 0.0, 0.0, 0.0])
    positions = pd.Series([0.0, 1.0, 1.0, 0.0])  # 매수, 보유, 매도
    out = apply_cost(returns, positions, instrument_type="crypto")
    assert out.iloc[1] == pytest.approx(-COST_CRYPTO_PER_SIDE)  # buy
    assert out.iloc[2] == pytest.approx(0.0)                     # hold
    assert out.iloc[3] == pytest.approx(-COST_CRYPTO_PER_SIDE)   # sell


# ---------------------------------------------------------------------------
# KRX: 비대칭 (buy 0.015%, sell 0.245%)
# ---------------------------------------------------------------------------

def test_krx_buy_subtracts_15bp():
    returns = pd.Series([0.0, 0.0])
    positions = pd.Series([0.0, 1.0])
    out = apply_cost(returns, positions, instrument_type="krx")
    assert out.iloc[1] == pytest.approx(-COST_KRX_BUY)
    assert COST_KRX_BUY == pytest.approx(0.00015)


def test_krx_sell_subtracts_245bp():
    returns = pd.Series([0.0, 0.0])
    positions = pd.Series([1.0, 0.0])
    out = apply_cost(returns, positions, instrument_type="krx")
    assert out.iloc[1] == pytest.approx(-COST_KRX_SELL)
    assert COST_KRX_SELL == pytest.approx(0.00245)


def test_krx_buy_sell_asymmetric():
    """KRX 매도가 매수보다 ~16배 비싸야 함 (거래세 효과)."""
    returns = pd.Series([0.0, 0.0, 0.0])
    buy_only = apply_cost(returns, pd.Series([0.0, 1.0, 1.0]), "krx")
    sell_only = apply_cost(returns, pd.Series([1.0, 0.0, 0.0]), "krx")
    assert abs(sell_only.iloc[1]) > abs(buy_only.iloc[1]) * 10


def test_krx_with_real_returns():
    returns = pd.Series([0.01, 0.02])
    positions = pd.Series([0.0, 1.0])
    out = apply_cost(returns, positions, instrument_type="krx")
    assert out.iloc[0] == pytest.approx(0.01)  # delta=NaN→0, no cost
    assert out.iloc[1] == pytest.approx(0.02 - COST_KRX_BUY)


# ---------------------------------------------------------------------------
# 일반 속성
# ---------------------------------------------------------------------------

def test_output_index_preserved():
    idx = pd.date_range("2026-01-01", periods=4, freq="D")
    returns = pd.Series([0.0, 0.0, 0.0, 0.0], index=idx)
    positions = pd.Series([0.0, 1.0, 1.0, 0.0], index=idx)
    out = apply_cost(returns, positions, instrument_type="crypto")
    assert (out.index == idx).all()


def test_output_length_matches_input():
    returns = pd.Series([0.0] * 5)
    positions = pd.Series([0.0, 0.5, 1.0, 0.5, 0.0])
    out = apply_cost(returns, positions, instrument_type="krx")
    assert len(out) == 5


def test_first_bar_delta_is_zero_via_fillna():
    """첫 bar 의 positions.diff() 는 NaN → fillna(0) → 비용 0."""
    returns = pd.Series([0.05])
    positions = pd.Series([1.0])  # 첫 bar 부터 풀 포지션
    out = apply_cost(returns, positions, instrument_type="crypto")
    assert out.iloc[0] == pytest.approx(0.05)  # 비용 차감 없음
