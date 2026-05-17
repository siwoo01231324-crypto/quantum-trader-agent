"""#238 Item 5 (safe core) — logical vs exchange position reconciliation.

Root sin of the incident: Binance Futures one-way mode holds exactly ONE net
position per symbol, but the system tracks per-strategy *logical* positions
(StrategyPositionStore) and the dashboard rendered those as if real. A
live-scanner "+0.05 BTC long" netted away by momo's -1 short does not exist
on the exchange. This module makes that divergence VISIBLE instead of silent.
It does NOT re-architect attribution — it is the reconciliation safety net.
"""
from __future__ import annotations

from decimal import Decimal

from src.live.position_reconciliation import (
    ReconcileMismatch,
    reconcile_positions,
    sum_logical_by_symbol,
)


def test_sum_logical_collapses_strategies_per_symbol():
    logical = {
        "momo-btc-v2": {"BTCUSDT": Decimal("-1")},
        "live-bb": {"BTCUSDT": Decimal("0.05")},
        "live-macd": {"BTCUSDT": Decimal("0.05")},
        "live-breakout": {"BNBUSDT": Decimal("0.1")},
    }
    assert sum_logical_by_symbol(logical) == {
        "BTCUSDT": Decimal("-0.90"),
        "BNBUSDT": Decimal("0.1"),
    }


def test_reconcile_flags_btc_divergence_from_incident():
    """The exact incident shape: logical -0.90 vs Binance actual -0.85."""
    logical = {
        "momo-btc-v2": {"BTCUSDT": Decimal("-1")},
        "live-bb": {"BTCUSDT": Decimal("0.05")},
        "live-macd": {"BTCUSDT": Decimal("0.05")},
        "live-breakout": {"BNBUSDT": Decimal("0.1")},
    }
    broker_net = {"BTCUSDT": Decimal("-0.85"), "BNBUSDT": Decimal("0.1")}
    out = reconcile_positions(logical, broker_net, tol=Decimal("0.001"))
    assert out == [
        ReconcileMismatch(
            symbol="BTCUSDT",
            logical_net=Decimal("-0.90"),
            broker_net=Decimal("-0.85"),
            delta=Decimal("-0.05"),
        )
    ]


def test_within_tolerance_is_not_flagged():
    logical = {"s": {"BTCUSDT": Decimal("0.1000")}}
    broker_net = {"BTCUSDT": Decimal("0.0999")}
    assert reconcile_positions(logical, broker_net, tol=Decimal("0.001")) == []


def test_symbol_present_only_on_exchange_is_flagged():
    """A position on the exchange that no strategy claims (manual / orphan)."""
    out = reconcile_positions({}, {"ETHUSDT": Decimal("2")}, tol=Decimal("0"))
    # delta = logical_net - broker_net = 0 - 2 = -2 (logical is short vs exchange)
    assert out == [
        ReconcileMismatch(
            symbol="ETHUSDT",
            logical_net=Decimal("0"),
            broker_net=Decimal("2"),
            delta=Decimal("-2"),
        )
    ]


def test_symbol_present_only_in_logical_is_flagged():
    """Logical thinks it holds something the exchange has flat (phantom)."""
    out = reconcile_positions(
        {"s": {"XRPUSDT": Decimal("-3")}}, {}, tol=Decimal("0")
    )
    assert out == [
        ReconcileMismatch(
            symbol="XRPUSDT",
            logical_net=Decimal("-3"),
            broker_net=Decimal("0"),
            delta=Decimal("-3"),
        )
    ]


def test_perfectly_matched_book_returns_empty():
    logical = {"a": {"BTCUSDT": Decimal("1")}, "b": {"BTCUSDT": Decimal("-1")}}
    assert reconcile_positions(logical, {"BTCUSDT": Decimal("0")},
                               tol=Decimal("0")) == []


def test_zero_net_both_sides_not_flagged_even_with_zero_tol():
    """Flat symbol on both sides must never raise a mismatch."""
    assert reconcile_positions(
        {"a": {"BTCUSDT": Decimal("0")}}, {"BTCUSDT": Decimal("0")},
        tol=Decimal("0"),
    ) == []
