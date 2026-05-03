"""Tests for SquareRootImpact slippage model and MockMatchingEngine integration."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.brokers.base import OrderRequest, OrderType
from src.execution.base import MarketState, Side, Tick, TimeInForce
from src.execution.mock_matching import MockMatchingEngine, SquareRootImpact


def _tick(
    bid: float = 99.0,
    ask: float = 101.0,
    last: float = 100.0,
    volume: int = 1_000_000,
) -> Tick:
    return Tick(
        symbol="BTCUSDT",
        bid=bid,
        ask=ask,
        last=last,
        volume=volume,
        ts=datetime.now(timezone.utc),
    )


def _market_state(adv: float = 1_000_000.0, **tick_kwargs) -> MarketState:
    return MarketState(tick=_tick(**tick_kwargs), adv=adv)


def _order(
    side: Side,
    qty: str = "1",
    order_type: OrderType = OrderType.MARKET,
    price: str | None = None,
) -> OrderRequest:
    return OrderRequest(
        client_order_id="cid-001",
        symbol="BTCUSDT",
        side=side,
        qty=Decimal(qty),
        order_type=order_type,
        price=Decimal(price) if price is not None else None,
        tif=TimeInForce.GTC,
    )


# ---------------------------------------------------------------------------
# SquareRootImpact unit tests
# ---------------------------------------------------------------------------

class TestSquareRootImpact:
    def test_default_params(self):
        model = SquareRootImpact()
        assert model.k == Decimal("1.0")
        assert model.sigma == Decimal("0.01")

    def test_zero_adv_returns_zero(self):
        """ADV=0 should not raise; returns zero impact."""
        model = SquareRootImpact()
        state = _market_state(adv=0.0)
        impact = model.estimate(side=Side.BUY, qty=Decimal("1"), mid=Decimal("100"), market=state)
        assert impact == Decimal("0")

    def test_buy_impact_positive(self):
        """BUY impact should be positive (price moves up)."""
        model = SquareRootImpact(k=Decimal("1.0"), sigma=Decimal("0.01"))
        state = _market_state(adv=1_000_000.0)
        impact = model.estimate(side=Side.BUY, qty=Decimal("100"), mid=Decimal("100"), market=state)
        assert impact > Decimal("0")

    def test_sell_impact_negative(self):
        """SELL impact should be negative (price moves down)."""
        model = SquareRootImpact(k=Decimal("1.0"), sigma=Decimal("0.01"))
        state = _market_state(adv=1_000_000.0)
        impact = model.estimate(side=Side.SELL, qty=Decimal("100"), mid=Decimal("100"), market=state)
        assert impact < Decimal("0")

    def test_impact_formula(self):
        """Verify: impact = side_sign * k * sigma * mid * sqrt(qty/ADV)."""
        k = Decimal("1.0")
        sigma = Decimal("0.01")
        mid = Decimal("100")
        qty = Decimal("1000")
        adv = Decimal("1000000")

        model = SquareRootImpact(k=k, sigma=sigma)
        state = _market_state(adv=float(adv))
        impact = model.estimate(side=Side.BUY, qty=qty, mid=mid, market=state)

        expected = k * sigma * mid * Decimal(str(math.sqrt(float(qty / adv))))
        assert abs(impact - expected) < Decimal("0.000001")

    def test_larger_qty_larger_impact(self):
        """Impact scales with sqrt(qty): doubling qty → ~1.41x impact."""
        model = SquareRootImpact()
        state = _market_state(adv=1_000_000.0)
        mid = Decimal("100")

        impact1 = model.estimate(side=Side.BUY, qty=Decimal("100"), mid=mid, market=state)
        impact2 = model.estimate(side=Side.BUY, qty=Decimal("400"), mid=mid, market=state)

        ratio = float(impact2 / impact1)
        assert abs(ratio - 2.0) < 0.001  # sqrt(400)/sqrt(100) = 2.0

    def test_impact_returns_decimal(self):
        model = SquareRootImpact()
        state = _market_state(adv=1_000_000.0)
        impact = model.estimate(side=Side.BUY, qty=Decimal("1"), mid=Decimal("100"), market=state)
        assert isinstance(impact, Decimal)

    def test_custom_k_scales_impact(self):
        """Doubling k should double the impact."""
        state = _market_state(adv=1_000_000.0)
        mid = Decimal("100")
        qty = Decimal("100")

        impact1 = SquareRootImpact(k=Decimal("1.0")).estimate(
            side=Side.BUY, qty=qty, mid=mid, market=state
        )
        impact2 = SquareRootImpact(k=Decimal("2.0")).estimate(
            side=Side.BUY, qty=qty, mid=mid, market=state
        )
        assert abs(impact2 / impact1 - 2) < Decimal("0.001")


# ---------------------------------------------------------------------------
# MockMatchingEngine integration with SquareRootImpact
# ---------------------------------------------------------------------------

class TestMockMatchingEngineWithSlippage:
    def test_no_slippage_model_zero_impact(self):
        """Default engine (no slippage_model) fills at mid — regression guard."""
        engine = MockMatchingEngine()
        state = _market_state(adv=1_000_000.0, last=100.0)
        fills = engine.match(_order(Side.BUY), state)
        assert fills[0].price == Decimal("100")

    def test_buy_fill_price_above_mid(self):
        """With SquareRootImpact, BUY fill price > mid."""
        model = SquareRootImpact(k=Decimal("1.0"), sigma=Decimal("0.01"))
        engine = MockMatchingEngine(slippage_model=model)
        state = _market_state(adv=1_000_000.0, last=100.0)
        fills = engine.match(_order(Side.BUY, qty="1000"), state)
        assert len(fills) == 1
        assert fills[0].price > Decimal("100")

    def test_sell_fill_price_below_mid(self):
        """With SquareRootImpact, SELL fill price < mid."""
        model = SquareRootImpact(k=Decimal("1.0"), sigma=Decimal("0.01"))
        engine = MockMatchingEngine(slippage_model=model)
        state = _market_state(adv=1_000_000.0, last=100.0)
        fills = engine.match(_order(Side.SELL, qty="1000"), state)
        assert len(fills) == 1
        assert fills[0].price < Decimal("100")

    def test_fill_price_is_decimal(self):
        """Fill price must remain Decimal after slippage applied."""
        model = SquareRootImpact()
        engine = MockMatchingEngine(slippage_model=model)
        state = _market_state(adv=1_000_000.0, last=100.0)
        fills = engine.match(_order(Side.BUY), state)
        assert isinstance(fills[0].price, Decimal)

    def test_limit_order_with_slippage(self):
        """Limit fills also apply slippage when model is set."""
        model = SquareRootImpact(k=Decimal("1.0"), sigma=Decimal("0.01"))
        engine = MockMatchingEngine(slippage_model=model)
        state = _market_state(adv=1_000_000.0, bid=99.0, ask=101.0, last=100.0)
        fills = engine.match(
            _order(Side.BUY, qty="1000", order_type=OrderType.LIMIT, price="101"), state
        )
        assert len(fills) == 1
        assert fills[0].price > Decimal("100")

    def test_zero_adv_slippage_does_not_crash(self):
        """ADV=0 should not raise; fills at mid."""
        model = SquareRootImpact()
        engine = MockMatchingEngine(slippage_model=model)
        state = _market_state(adv=0.0, last=100.0)
        fills = engine.match(_order(Side.BUY), state)
        assert len(fills) == 1
        assert fills[0].price == Decimal("100")

    def test_seed_reproducibility(self):
        """With deterministic inputs, fill prices are identical across calls."""
        model = SquareRootImpact(k=Decimal("1.0"), sigma=Decimal("0.01"))
        engine = MockMatchingEngine(slippage_model=model)
        state = _market_state(adv=1_000_000.0, last=100.0)
        order = _order(Side.BUY, qty="500")

        fills1 = engine.match(order, state)
        fills2 = engine.match(order, state)
        assert fills1[0].price == fills2[0].price

    def test_slippage_scales_with_qty(self):
        """Larger qty → larger price impact in the fill."""
        model = SquareRootImpact(k=Decimal("1.0"), sigma=Decimal("0.01"))
        engine = MockMatchingEngine(slippage_model=model)
        state = _market_state(adv=1_000_000.0, last=100.0)

        fills_small = engine.match(_order(Side.BUY, qty="100"), state)
        fills_large = engine.match(_order(Side.BUY, qty="10000"), state)
        assert fills_large[0].price > fills_small[0].price
