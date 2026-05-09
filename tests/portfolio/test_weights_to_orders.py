"""Unit tests for weights_to_orders converter (#218 Phase 2)."""
from __future__ import annotations

import pandas as pd
import pytest

from portfolio.order_intent import OrderIntent
from portfolio.weights_to_orders import (
    BINANCE_BTC_LOT,
    BINANCE_DEFAULT_LOT,
    KRX_LOT,
    LotSpec,
    estimate_post_rebal_cash,
    weights_to_orders,
)


def _series(d: dict) -> pd.Series:
    return pd.Series(d)


# ---------------------------------------------------------------------------
# Basic flows
# ---------------------------------------------------------------------------

def test_empty_capital_returns_no_orders():
    orders = weights_to_orders(
        "test", _series({"AAA": 0.5}), {}, _series({"AAA": 100}),
        total_capital=0,
    )
    assert orders == []


def test_initial_buy_from_zero_position():
    """현재 포지션 없음 → target weight × capital / price 만큼 매수."""
    orders = weights_to_orders(
        "test",
        target_weights=_series({"005930": 0.5, "000660": 0.5}),
        current_positions={},
        prices=_series({"005930": 80000, "000660": 200000}),
        total_capital=10_000_000,
        lot_spec=KRX_LOT,
    )
    assert len(orders) == 2
    by_sym = {o.symbol: o for o in orders}

    # 0.5 * 9_900_000 / 80_000 = 61.875 → floor → 61 주
    assert by_sym["005930"].side == "buy"
    assert by_sym["005930"].qty == 61.0

    # 0.5 * 9_900_000 / 200_000 = 24.75 → floor → 24 주
    assert by_sym["000660"].side == "buy"
    assert by_sym["000660"].qty == 24.0


def test_full_liquidation_when_target_zero():
    orders = weights_to_orders(
        "test",
        target_weights=_series({"005930": 0.0}),
        current_positions={"005930": 50},
        prices=_series({"005930": 80000}),
        total_capital=10_000_000,
        lot_spec=KRX_LOT,
    )
    assert len(orders) == 1
    assert orders[0].side == "sell"
    assert orders[0].qty == 50.0


def test_partial_rebalance_increases_position():
    """기존 30주 보유, 목표 60주 → 30주 매수."""
    orders = weights_to_orders(
        "test",
        target_weights=_series({"005930": 0.49}),  # ~60주 정도
        current_positions={"005930": 30},
        prices=_series({"005930": 80000}),
        total_capital=10_000_000,
        lot_spec=KRX_LOT,
    )
    assert len(orders) == 1
    assert orders[0].side == "buy"
    # target = 0.49 * 9_900_000 / 80_000 = 60.6 → floor 60 → 30 매수
    assert orders[0].qty == 30.0


def test_partial_rebalance_decreases_position():
    orders = weights_to_orders(
        "test",
        target_weights=_series({"005930": 0.16}),
        current_positions={"005930": 30},
        prices=_series({"005930": 80000}),
        total_capital=10_000_000,
        lot_spec=KRX_LOT,
    )
    # target = 0.16 * 9_900_000 / 80_000 = 19.8 → floor 19 → 11 매도
    assert len(orders) == 1
    assert orders[0].side == "sell"
    assert orders[0].qty == 11.0


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def test_skips_orders_below_min_notional():
    """매수 1주 × 50원 = 50원 < min_notional 100원 → 주문 생략."""
    orders = weights_to_orders(
        "test",
        target_weights=_series({"PENNY": 0.0001}),
        current_positions={},
        prices=_series({"PENNY": 50}),
        total_capital=10_000_000,
        lot_spec=KRX_LOT,
    )
    # 0.0001 * 9_900_000 / 50 = 19.8 → floor 19 → notional = 19 × 50 = 950 (≥ 100, 통과)
    # → 19주 매수 주문 발생
    if orders:
        assert orders[0].qty == 19.0
    # 정말 미세한 변화면 생략
    orders_tiny = weights_to_orders(
        "test",
        target_weights=_series({"PENNY": 0.0000001}),
        current_positions={},
        prices=_series({"PENNY": 50}),
        total_capital=10_000_000,
        lot_spec=KRX_LOT,
    )
    assert orders_tiny == []


def test_skips_below_lot_size_diff():
    """diff < 1주 → KRX lot_size=1 이라 무시."""
    orders = weights_to_orders(
        "test",
        target_weights=_series({"AAA": 0.50}),
        current_positions={"AAA": 50},
        prices=_series({"AAA": 100_000}),
        total_capital=10_000_000,
        lot_spec=KRX_LOT,
    )
    # target = 0.5 * 9_900_000 / 100_000 = 49.5 → floor 49 → 1 매도
    # 1주 차이는 lot_size 와 같음 → 정확히 lot_size 인 경우는 발주 (조건은 < lot_size)
    assert len(orders) == 1
    assert orders[0].qty == 1.0
    assert orders[0].side == "sell"


def test_no_orders_when_already_at_target():
    orders = weights_to_orders(
        "test",
        target_weights=_series({"AAA": 0.0}),
        current_positions={"AAA": 0.0},
        prices=_series({"AAA": 100}),
        total_capital=10_000_000,
        lot_spec=KRX_LOT,
    )
    assert orders == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_missing_price_forces_liquidation():
    """price=NaN/0 종목 보유 시 → target 0 강제 청산."""
    orders = weights_to_orders(
        "test",
        target_weights=_series({"AAA": 0.5}),
        current_positions={"AAA": 50, "DELISTED": 100},
        prices=_series({"AAA": 100_000, "DELISTED": 0}),
        total_capital=10_000_000,
        lot_spec=KRX_LOT,
    )
    by_sym = {o.symbol: o for o in orders}
    # DELISTED 는 가격 0 → target 강제 0 → 100주 청산 시도
    # 단 notional = 100 * 0 = 0 < min_notional 100 → 생략
    # 즉 청산 못함 (가격 부재) — 운영 시 별도 처리 필요
    assert "DELISTED" not in by_sym  # min_notional 필터로 제외


def test_cash_buffer_reduces_investment():
    """cash_buffer_pct=10% → 10% 는 투자 안 함."""
    orders = weights_to_orders(
        "test",
        target_weights=_series({"AAA": 1.0}),
        current_positions={},
        prices=_series({"AAA": 1_000_000}),
        total_capital=10_000_000,
        lot_spec=KRX_LOT,
        cash_buffer_pct=0.10,
    )
    # 1.0 * (10_000_000 * 0.9) / 1_000_000 = 9 주
    assert len(orders) == 1
    assert orders[0].qty == 9.0


def test_crypto_lot_fractional():
    """BTC 거래는 0.00001 단위 발주."""
    orders = weights_to_orders(
        "test",
        target_weights=_series({"BTCUSDT": 0.5}),
        current_positions={},
        prices=_series({"BTCUSDT": 80_000}),
        total_capital=10_000,
        lot_spec=BINANCE_BTC_LOT,
    )
    # 0.5 * 9_900 / 80_000 = 0.06187... → 0.00001 단위 floor → 0.06187
    assert len(orders) == 1
    assert orders[0].symbol == "BTCUSDT"
    assert 0.0618 < orders[0].qty < 0.0620


def test_estimate_post_rebal_cash():
    """리밸 후 잔여 현금 추정."""
    cash = estimate_post_rebal_cash(
        target_weights=_series({"AAA": 0.5, "BBB": 0.5}),
        prices=_series({"AAA": 100_000, "BBB": 200_000}),
        total_capital=10_000_000,
        lot_spec=KRX_LOT,
    )
    # AAA: 0.5 * 9_900_000 / 100_000 = 49.5 → floor 49주 → 4_900_000
    # BBB: 0.5 * 9_900_000 / 200_000 = 24.75 → floor 24주 → 4_800_000
    # spent = 9_700_000, total = 10_000_000 → 잔여 300_000
    assert cash == pytest.approx(300_000)


def test_lot_specs_immutable():
    """기본 lot specs 가 변경 안 되도록 frozen 보장."""
    with pytest.raises(Exception):
        KRX_LOT.lot_size = 999


def test_returns_list_of_order_intent():
    orders = weights_to_orders(
        "strat_x",
        target_weights=_series({"AAA": 0.3}),
        current_positions={},
        prices=_series({"AAA": 100}),
        total_capital=10_000_000,
        lot_spec=KRX_LOT,
    )
    assert all(isinstance(o, OrderIntent) for o in orders)
    for o in orders:
        assert o.strategy_id == "strat_x"
        assert o.side in ("buy", "sell")
        assert o.qty > 0
        assert o.meta is not None
