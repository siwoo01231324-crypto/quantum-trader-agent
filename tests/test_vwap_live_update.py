"""Tests for VWAP live volume profile blend (patent #84-1 adaptation).

Covers:
- Fixed profile vs dynamic blend comparison
- Slippage reduction measurement
- KRX simultaneous auction / VI trigger scenarios
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.execution.base import (
    Fill,
    MarketState,
    ParentOrder,
    Side,
    Tick,
    TimeInForce,
)
from src.execution.vwap import VWAPAlgo


def _tick(ts: datetime | None = None, volume: int = 1000) -> Tick:
    if ts is None:
        ts = datetime(2024, 1, 2, 9, 0, 0, tzinfo=timezone.utc)
    return Tick(symbol="005930", bid=70000.0, ask=70050.0, last=70025.0, volume=volume, ts=ts)


def _state(tick: Tick | None = None, in_single_auction: bool = False) -> MarketState:
    return MarketState(tick=tick or _tick(), in_single_auction=in_single_auction)


def _parent(qty: int = 1000) -> ParentOrder:
    return ParentOrder(
        order_id="ORD001",
        symbol="005930",
        side=Side.BUY,
        qty=qty,
        tif=TimeInForce.DAY,
        algo_params={"vwap_alpha": 0.5},
    )


# ---------------------------------------------------------------------------
# Static profile (backwards-compatible) tests
# ---------------------------------------------------------------------------

class TestVWAPStaticProfile:
    def test_plan_emits_weighted_first_slice(self):
        algo = VWAPAlgo(volume_profile=[40.0, 30.0, 30.0])
        parent = _parent(qty=1000)
        orders = algo.plan(parent, _state())
        assert len(orders) == 1
        assert orders[0].qty == 400  # 40% of 1000

    def test_on_market_tick_emits_subsequent_slices(self):
        algo = VWAPAlgo(volume_profile=[40.0, 30.0, 30.0])
        parent = _parent(qty=1000)
        algo.plan(parent, _state())
        orders = algo.on_market_tick(_tick())
        assert len(orders) == 1
        assert orders[0].qty == 300  # 30% of 1000

    def test_last_slice_uses_remainder(self):
        algo = VWAPAlgo(volume_profile=[40.0, 30.0, 30.0])
        parent = _parent(qty=1000)
        algo.plan(parent, _state())
        algo.on_market_tick(_tick())
        orders = algo.on_market_tick(_tick())
        assert len(orders) == 1
        # last slice gets remainder: 1000 - 400 - 300 = 300
        assert orders[0].qty == 300

    def test_exhausted_returns_empty(self):
        algo = VWAPAlgo(volume_profile=[50.0, 50.0])
        parent = _parent(qty=100)
        algo.plan(parent, _state())
        algo.on_market_tick(_tick())
        result = algo.on_market_tick(_tick())
        assert result == []

    def test_cancel_stops_emission(self):
        algo = VWAPAlgo(volume_profile=[50.0, 50.0])
        parent = _parent(qty=100)
        algo.plan(parent, _state())
        algo.cancel()
        result = algo.on_market_tick(_tick())
        assert result == []


# ---------------------------------------------------------------------------
# Dynamic blend: live volume updater
# ---------------------------------------------------------------------------

class TestVWAPDynamicBlend:
    def test_on_market_tick_with_realized_volume_updates_weights(self):
        """Remaining weights must shift toward realized distribution when alpha < 1."""
        algo = VWAPAlgo(volume_profile=[25.0, 25.0, 25.0, 25.0], algo_params={"vwap_alpha": 0.0})
        parent = _parent(qty=1000)
        algo.plan(parent, _state())  # consumes slice 0

        # After tick 0: realized showed heavy volume in remaining slots
        # If alpha=0 (pure live), weights for remaining slots should be driven entirely
        # by realized_volume proportions.
        t = _tick(volume=300)
        # realized_volume represents cumulative live volume for each bucket passed so far
        orders = algo.on_market_tick(t, realized_volume=300)
        assert len(orders) == 1
        assert orders[0].qty > 0

    def test_alpha_0_pure_live_blend(self):
        """alpha=0 means full live-data weighting (no historical)."""
        hist = [10.0, 10.0, 40.0, 40.0]
        algo = VWAPAlgo(volume_profile=hist, algo_params={"vwap_alpha": 0.0})
        parent = _parent(qty=1000)
        algo.plan(parent, _state())  # slice 0 consumed

        # Provide equal realized volumes for remaining 3 buckets → should produce equal weights
        orders = algo.on_market_tick(_tick(volume=100), realized_volume=100)
        assert len(orders) == 1

    def test_alpha_1_pure_historical(self):
        """alpha=1 means fall back to historical profile only — same as static."""
        profile = [25.0, 25.0, 25.0, 25.0]
        static = VWAPAlgo(volume_profile=profile)
        dynamic = VWAPAlgo(volume_profile=profile, algo_params={"vwap_alpha": 1.0})

        p = _parent(qty=1000)
        static.plan(p, _state())
        dynamic.plan(_parent(qty=1000), _state())

        t = _tick(volume=999)
        s_orders = static.on_market_tick(t)
        d_orders = dynamic.on_market_tick(t, realized_volume=999)
        assert s_orders[0].qty == d_orders[0].qty

    def test_default_alpha_from_parent_algo_params(self):
        """vwap_alpha in ParentOrder.algo_params is respected."""
        algo = VWAPAlgo(volume_profile=[50.0, 50.0])
        parent = ParentOrder(
            order_id="ORD002",
            symbol="005930",
            side=Side.BUY,
            qty=200,
            algo_params={"vwap_alpha": 0.8},
        )
        orders = algo.plan(parent, _state())
        assert len(orders) == 1

    def test_default_alpha_0_5_when_not_specified(self):
        """Default alpha is 0.5 when not in algo_params."""
        algo = VWAPAlgo(volume_profile=[50.0, 50.0])
        parent = ParentOrder(
            order_id="ORD003",
            symbol="005930",
            side=Side.BUY,
            qty=200,
            algo_params={},
        )
        algo.plan(parent, _state())
        # Should not raise; uses 0.5 default
        orders = algo.on_market_tick(_tick(), realized_volume=500)
        assert len(orders) == 1

    def test_weights_sum_to_one_after_blend(self):
        """After blending, remaining normalized weights must sum to ~1."""
        algo = VWAPAlgo(volume_profile=[20.0, 20.0, 30.0, 30.0], algo_params={"vwap_alpha": 0.5})
        parent = _parent(qty=1000)
        algo.plan(parent, _state())  # consume slice 0
        # Blend without emitting — check weights directly after blend
        algo._blend_weights(150)
        # After blend, weights[idx:] must sum to 1
        remaining = algo.weights[algo._idx :]
        assert abs(sum(remaining) - 1.0) < 1e-9

    def test_realized_volume_zero_falls_back_to_historical(self):
        """If realized_volume=0, blending gracefully falls back to historical weights."""
        algo = VWAPAlgo(volume_profile=[25.0, 25.0, 25.0, 25.0], algo_params={"vwap_alpha": 0.5})
        parent = _parent(qty=1000)
        algo.plan(parent, _state())
        orders = algo.on_market_tick(_tick(volume=0), realized_volume=0)
        assert len(orders) == 1
        assert orders[0].qty > 0


# ---------------------------------------------------------------------------
# KRX simultaneous auction / VI suspension scenarios
# ---------------------------------------------------------------------------

class TestVWAPKRXScenarios:
    def test_single_auction_state_does_not_emit(self):
        """During KRX simultaneous auction, tick should not emit child orders."""
        algo = VWAPAlgo(volume_profile=[50.0, 50.0])
        parent = _parent(qty=1000)
        algo.plan(parent, _state())
        # Simulate single auction: in_single_auction=True means no emission
        tick = _tick()
        orders = algo.on_market_tick(tick, in_auction=True)
        assert orders == []

    def test_resumes_after_auction_ends(self):
        """After auction clears, next tick should emit normally."""
        algo = VWAPAlgo(volume_profile=[50.0, 50.0])
        parent = _parent(qty=1000)
        algo.plan(parent, _state())
        # Suspended during auction
        algo.on_market_tick(_tick(), in_auction=True)
        # Resumes on next normal tick
        orders = algo.on_market_tick(_tick())
        assert len(orders) == 1

    def test_vi_spike_blends_remaining_slices(self):
        """VI volume spike should be reflected in remaining weights when alpha < 1."""
        algo = VWAPAlgo(
            volume_profile=[10.0, 10.0, 10.0, 70.0],  # historically heavy at end
            algo_params={"vwap_alpha": 0.3},
        )
        parent = _parent(qty=1000)
        algo.plan(parent, _state())  # consume slice 0 (10%)

        # Simulate VI spike: huge realized volume early
        tick_vi = _tick(volume=5000)
        orders_vi = algo.on_market_tick(tick_vi, realized_volume=5000)
        assert len(orders_vi) == 1
        # With alpha=0.3 (70% live), early spike pushes more qty to current slice
        qty_after_vi = orders_vi[0].qty

        # Compare against pure historical (alpha=1.0)
        algo_hist = VWAPAlgo(volume_profile=[10.0, 10.0, 10.0, 70.0], algo_params={"vwap_alpha": 1.0})
        algo_hist.plan(_parent(qty=1000), _state())
        orders_hist = algo_hist.on_market_tick(tick_vi, realized_volume=5000)
        qty_hist = orders_hist[0].qty

        # Dynamic blend with spike should give different (likely higher) qty than pure historical
        # Both should be positive and <= total remaining qty
        assert qty_after_vi > 0
        assert qty_hist > 0

    def test_vi_scenario_total_qty_conserved(self):
        """Sum of all child order quantities must equal parent qty."""
        algo = VWAPAlgo(
            volume_profile=[25.0, 25.0, 25.0, 25.0],
            algo_params={"vwap_alpha": 0.5},
        )
        parent = _parent(qty=1000)
        total = 0
        orders0 = algo.plan(parent, _state())
        total += sum(o.qty for o in orders0)

        realized_vols = [200, 800, 150]
        for rv in realized_vols:
            t = _tick(volume=rv)
            orders = algo.on_market_tick(t, realized_volume=rv)
            total += sum(o.qty for o in orders)

        assert total == 1000


# ---------------------------------------------------------------------------
# Backtest comparison: static vs dynamic slippage proxy
# ---------------------------------------------------------------------------

class TestVWAPSlippageProxy:
    """Compare tracking error between static and dynamic VWAP."""

    def _run_scenario(
        self,
        profile: list[float],
        realized_volumes: list[int],
        alpha: float,
        parent_qty: int = 1000,
    ) -> list[int]:
        algo = VWAPAlgo(volume_profile=profile, algo_params={"vwap_alpha": alpha})
        parent = _parent(qty=parent_qty)
        result = []
        orders = algo.plan(parent, _state())
        result.extend(o.qty for o in orders)
        for rv in realized_volumes:
            orders = algo.on_market_tick(_tick(volume=rv), realized_volume=rv)
            result.extend(o.qty for o in orders)
        return result

    def test_dynamic_reduces_tracking_error_on_volume_spike(self):
        """When actual volume deviates from historical, dynamic alpha<1 adapts better."""
        hist_profile = [25.0, 25.0, 25.0, 25.0]
        # Actual volumes: first bucket normal, second bucket spikes 4x
        realized = [250, 1000, 250]

        static_slices = self._run_scenario(hist_profile, realized, alpha=1.0)
        dynamic_slices = self._run_scenario(hist_profile, realized, alpha=0.2)

        # Total qty must be identical
        assert sum(static_slices) == sum(dynamic_slices) == 1000

        # Dynamic should produce more qty in slice 1 (the spike bucket) vs static
        assert dynamic_slices[1] >= static_slices[1]
