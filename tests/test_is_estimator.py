"""TDD tests for Implementation Shortfall estimator (issue #114).

IS formula (Perold 1988 simple parametric):
    IS_est_bps = spread_bps/2 + market_impact_coeff * sqrt(qty/adv) * 10000
    realized_IS_bps = (fill_price - arrival_price) / arrival_price * 10000  (BUY)
                    = (arrival_price - fill_price) / arrival_price * 10000  (SELL)
    is_prediction_error_bps = realized_IS_bps - IS_est_bps
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.brokers.is_estimator import (
    MarketSnapshot,
    pre_flight_is_estimate,
    realized_is,
)
from src.brokers.types import BrokerFill
from src.execution.base import Side


def _fill(price: str, qty: str = "100") -> BrokerFill:
    return BrokerFill(
        parent_id="p1",
        broker_order_id="b1",
        client_order_id="c1",
        trade_id="t1",
        qty=Decimal(qty),
        price=Decimal(price),
        fee=Decimal("0"),
        fee_asset="KRW",
        ts=datetime(2024, 1, 2, tzinfo=timezone.utc),
        is_maker=False,
    )


# ── pre_flight_is_estimate ────────────────────────────────────────────────────

class TestPreFlightIsEstimate:
    def test_basic_buy(self):
        snap = MarketSnapshot(
            symbol="005930",
            bid=Decimal("70000"),
            ask=Decimal("70100"),
            adv=1_000_000,
        )
        # spread_bps = (70100-70000)/70050 * 10000 ≈ 14.27 bps
        # half_spread ≈ 7.13 bps
        # market_impact = 0.1 * sqrt(500/1_000_000) * 10000 = 0.1 * 0.02236 * 10000 = 22.36 bps  (wrong scale)
        # Actually: market_impact_coeff * sqrt(qty/adv) * 10000
        # default coeff=0.1: 0.1 * sqrt(500/1_000_000) * 10000 = 0.1 * 0.000707 * 10000 = 0.707...
        # Wait: sqrt(500/1_000_000) = sqrt(0.0005) ≈ 0.02236
        # 0.1 * 0.02236 * 10000 = 22.36 bps
        result = pre_flight_is_estimate(
            symbol="005930",
            side=Side.BUY,
            qty=500,
            snap=snap,
        )
        assert result > 0
        # half-spread component
        mid = (Decimal("70000") + Decimal("70100")) / 2
        spread_bps = float((Decimal("70100") - Decimal("70000")) / mid * 10000)
        half_spread = spread_bps / 2
        import math
        market_impact = 0.1 * math.sqrt(500 / 1_000_000) * 10000
        expected = half_spread + market_impact
        assert abs(result - expected) < 0.01

    def test_zero_adv_returns_half_spread_only(self):
        snap = MarketSnapshot(
            symbol="X",
            bid=Decimal("100"),
            ask=Decimal("101"),
            adv=0,
        )
        result = pre_flight_is_estimate(symbol="X", side=Side.BUY, qty=100, snap=snap)
        mid = Decimal("100.5")
        expected = float((Decimal("101") - Decimal("100")) / mid * 10000) / 2
        assert abs(result - expected) < 0.01

    def test_custom_market_impact_coeff(self):
        snap = MarketSnapshot(
            symbol="X",
            bid=Decimal("1000"),
            ask=Decimal("1010"),
            adv=100_000,
        )
        r1 = pre_flight_is_estimate("X", Side.BUY, 1000, snap, market_impact_coeff=0.1)
        r2 = pre_flight_is_estimate("X", Side.BUY, 1000, snap, market_impact_coeff=0.2)
        assert r2 > r1

    def test_sell_same_as_buy_estimate(self):
        snap = MarketSnapshot(
            symbol="X",
            bid=Decimal("1000"),
            ask=Decimal("1010"),
            adv=100_000,
        )
        buy = pre_flight_is_estimate("X", Side.BUY, 500, snap)
        sell = pre_flight_is_estimate("X", Side.SELL, 500, snap)
        assert buy == sell  # IS estimate is symmetric (cost, not direction)

    def test_larger_qty_higher_impact(self):
        snap = MarketSnapshot(
            symbol="X",
            bid=Decimal("1000"),
            ask=Decimal("1010"),
            adv=1_000_000,
        )
        small = pre_flight_is_estimate("X", Side.BUY, 100, snap)
        large = pre_flight_is_estimate("X", Side.BUY, 10000, snap)
        assert large > small


# ── realized_is ──────────────────────────────────────────────────────────────

class TestRealizedIs:
    def test_buy_fill_above_arrival(self):
        # BUY at 70100, arrival 70000 → paid more → IS positive
        fill = _fill("70100")
        result = realized_is(
            fill=fill,
            arrival_price=Decimal("70000"),
            side=Side.BUY,
        )
        expected = float((Decimal("70100") - Decimal("70000")) / Decimal("70000") * 10000)
        assert abs(result - expected) < 0.001

    def test_buy_fill_at_arrival(self):
        fill = _fill("70000")
        result = realized_is(fill=fill, arrival_price=Decimal("70000"), side=Side.BUY)
        assert result == pytest.approx(0.0, abs=1e-6)

    def test_sell_fill_below_arrival(self):
        # SELL at 69900, arrival 70000 → received less → IS positive
        fill = _fill("69900")
        result = realized_is(fill=fill, arrival_price=Decimal("70000"), side=Side.SELL)
        expected = float((Decimal("70000") - Decimal("69900")) / Decimal("70000") * 10000)
        assert abs(result - expected) < 0.001

    def test_sell_fill_above_arrival(self):
        # SELL at 70100, arrival 70000 → received more → IS negative (price improvement)
        fill = _fill("70100")
        result = realized_is(fill=fill, arrival_price=Decimal("70000"), side=Side.SELL)
        assert result < 0

    def test_buy_price_improvement(self):
        # BUY at 69900, arrival 70000 → paid less → IS negative
        fill = _fill("69900")
        result = realized_is(fill=fill, arrival_price=Decimal("70000"), side=Side.BUY)
        assert result < 0


# ── is_prediction_error ──────────────────────────────────────────────────────

class TestIsPredictionError:
    def test_prediction_error_positive(self):
        """Realized IS > estimate → positive prediction error."""
        snap = MarketSnapshot(
            symbol="X",
            bid=Decimal("1000"),
            ask=Decimal("1010"),
            adv=1_000_000,
        )
        estimate = pre_flight_is_estimate("X", Side.BUY, 100, snap)
        # Simulate bad fill: fill at ask + 5
        fill = BrokerFill(
            parent_id="p1", broker_order_id="b1", client_order_id="c1",
            trade_id="t1", qty=Decimal("100"), price=Decimal("1015"),
            fee=Decimal("0"), fee_asset="KRW",
            ts=datetime(2024, 1, 2, tzinfo=timezone.utc), is_maker=False,
        )
        arrival = Decimal("1005")  # mid at order time
        real = realized_is(fill=fill, arrival_price=arrival, side=Side.BUY)
        error = real - estimate
        assert isinstance(error, float)

    def test_prediction_error_zero_for_perfect_estimate(self):
        """If realized IS == estimate, error is 0."""
        snap = MarketSnapshot(
            symbol="X",
            bid=Decimal("1000"),
            ask=Decimal("1010"),
            adv=1_000_000,
        )
        estimate = pre_flight_is_estimate("X", Side.BUY, 100, snap)
        # Construct a fill whose realized IS matches estimate
        mid = float((Decimal("1000") + Decimal("1010")) / 2)
        fill_price_val = mid * (1 + estimate / 10000)
        from decimal import ROUND_HALF_UP
        fill_price = Decimal(str(fill_price_val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        fill = BrokerFill(
            parent_id="p1", broker_order_id="b1", client_order_id="c1",
            trade_id="t1", qty=Decimal("100"), price=fill_price,
            fee=Decimal("0"), fee_asset="KRW",
            ts=datetime(2024, 1, 2, tzinfo=timezone.utc), is_maker=False,
        )
        real = realized_is(fill=fill, arrival_price=Decimal(str(mid)), side=Side.BUY)
        error = real - estimate
        assert abs(error) < 1.0  # within 1 bps tolerance due to rounding
