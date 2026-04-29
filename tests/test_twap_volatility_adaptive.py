"""Tests for TWAP volatility-adaptive slicing + KRX VI gate (issue #113).

Patent reference: US20210272201A1 (d) — rule-based vol-regime matching frequency
adjustment. ML engine (b) intentionally excluded.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.execution.base import (
    ChildOrder,
    Fill,
    MarketState,
    ParentOrder,
    Side,
    Tick,
    TimeInForce,
)
from src.execution.krx_handler import KRXSingleAuctionHandler, SingleAuctionPolicy
from src.execution.twap import VolatilityRegime, TWAPAlgo


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _tick(ts: datetime, bid: float = 100.0, ask: float = 100.2) -> Tick:
    return Tick(symbol="TEST", bid=bid, ask=ask, last=(bid + ask) / 2, volume=1000, ts=ts)


def _state(ts: datetime, *, halted: bool = False, in_single_auction: bool = False,
           bid: float = 100.0, ask: float = 100.2) -> MarketState:
    return MarketState(
        tick=_tick(ts, bid=bid, ask=ask),
        halted=halted,
        in_single_auction=in_single_auction,
    )


def _parent(qty: int = 100) -> ParentOrder:
    return ParentOrder(
        order_id="p1",
        symbol="TEST",
        side=Side.BUY,
        qty=qty,
    )


# ---------------------------------------------------------------------------
# 1. VolatilityRegime enum
# ---------------------------------------------------------------------------

class TestVolatilityRegime:
    def test_three_regimes_exist(self):
        assert VolatilityRegime.LOW is not None
        assert VolatilityRegime.MID is not None
        assert VolatilityRegime.HIGH is not None


# ---------------------------------------------------------------------------
# 2. TWAPAlgo — volatility_weight parameter
# ---------------------------------------------------------------------------

class TestTWAPVolatilityWeight:
    def test_default_no_weight_uniform(self):
        """Without vol weight, behavior is identical to existing uniform TWAP."""
        algo = TWAPAlgo(duration=timedelta(minutes=10), slice_count=4)
        t0 = datetime(2024, 1, 1, 9, 0, 0)
        orders = algo.plan(_parent(400), _state(t0))
        # first slice emitted immediately
        assert len(orders) == 1
        assert orders[0].qty == 100

    def test_volatility_weight_accepted(self):
        """TWAPAlgo accepts volatility_weight list without error."""
        algo = TWAPAlgo(
            duration=timedelta(minutes=10),
            slice_count=4,
            volatility_weight=[1.5, 1.0, 0.5, 0.5],
        )
        assert algo is not None

    def test_volatility_weight_length_must_match_slice_count(self):
        with pytest.raises(ValueError, match="volatility_weight"):
            TWAPAlgo(
                duration=timedelta(minutes=10),
                slice_count=4,
                volatility_weight=[1.0, 1.0],  # wrong length
            )

    def test_low_vol_emits_slice_earlier(self):
        """LOW vol regime: small weights on early slices → early slices compressed →
        later slices emit sooner than uniform.

        weights=[0.5, 0.5, 1.5, 1.5] (total=4.0):
          slice 0 boundary: 0.0   (always at start)
          slice 1 boundary: 0.5/4 = 0.125 → 1.25 min
          slice 2 boundary: 1.0/4 = 0.25  → 2.5 min  (uniform: 2.5 min, same)
          slice 3 boundary: 2.5/4 = 0.625 → 6.25 min

        weights=[2.0, 1.0, 0.5, 0.5] (total=4.0):
          slice 1 boundary: 2.0/4 = 0.5 → 5 min   (later than uniform 2.5 min)

        Use [0.5, 0.5, 1.5, 1.5]: slice 1 emits at 1.25 min (earlier than uniform 2.5 min).
        """
        algo_adaptive = TWAPAlgo(
            duration=timedelta(minutes=10),
            slice_count=4,
            volatility_weight=[0.5, 0.5, 1.5, 1.5],  # early slices compressed → emit sooner
        )
        algo_uniform = TWAPAlgo(duration=timedelta(minutes=10), slice_count=4)
        t0 = datetime(2024, 1, 1, 9, 0, 0)
        parent = _parent(400)
        state0 = _state(t0)

        adaptive_orders = algo_adaptive.plan(parent, state0)
        uniform_orders = algo_uniform.plan(parent, state0)

        # Both emit first slice at t0
        assert len(adaptive_orders) == 1
        assert len(uniform_orders) == 1

        # At 1.5 minutes: adaptive slice 1 boundary = 1.25 min → should emit
        # uniform slice 1 boundary = 2.5 min → should NOT emit
        t1 = t0 + timedelta(seconds=90)
        tick1 = _tick(t1)
        adaptive_extra = algo_adaptive.on_market_tick(tick1)
        uniform_extra = algo_uniform.on_market_tick(tick1)

        assert len(adaptive_extra) >= 1
        assert len(uniform_extra) == 0

    def test_high_vol_delays_slice(self):
        """HIGH vol regime: large weights on early slices → those slices take longer →
        later slices are delayed compared to uniform.

        weights=[2.0, 1.0, 0.5, 0.5] (total=4.0):
          slice 0 boundary: 0.0
          slice 1 boundary: 2.0/4 = 0.5 → 5 min  (uniform: 2.5 min)
          slice 2 boundary: 3.0/4 = 0.75 → 7.5 min
          slice 3 boundary: 3.5/4 = 0.875 → 8.75 min

        At 3 minutes (fraction=0.3), slice 1 boundary=0.5 → not yet → 0 extra.
        """
        algo = TWAPAlgo(
            duration=timedelta(minutes=10),
            slice_count=4,
            volatility_weight=[2.0, 1.0, 0.5, 0.5],  # heavy early → later slices delayed
        )
        t0 = datetime(2024, 1, 1, 9, 0, 0)
        algo.plan(_parent(400), _state(t0))

        # At 3 minutes (fraction=0.3 < 0.5 boundary for slice 1)
        t1 = t0 + timedelta(minutes=3)
        extra = algo.on_market_tick(_tick(t1))
        assert len(extra) == 0

    def test_all_slices_eventually_emitted(self):
        """All slices are emitted by end of duration regardless of vol weights."""
        algo = TWAPAlgo(
            duration=timedelta(minutes=10),
            slice_count=4,
            volatility_weight=[0.5, 0.5, 1.5, 1.5],
        )
        t0 = datetime(2024, 1, 1, 9, 0, 0)
        all_orders = algo.plan(_parent(400), _state(t0))

        # Advance to end of duration
        t_end = t0 + timedelta(minutes=10)
        all_orders += algo.on_market_tick(_tick(t_end))
        total_qty = sum(o.qty for o in all_orders)
        assert total_qty == 400

    def test_remainder_distributed_in_last_slice(self):
        """Remainder qty goes to last slice as in base TWAP."""
        algo = TWAPAlgo(
            duration=timedelta(minutes=10),
            slice_count=3,
            volatility_weight=[1.0, 1.0, 1.0],
        )
        t0 = datetime(2024, 1, 1, 9, 0, 0)
        all_orders = algo.plan(_parent(100), _state(t0))
        t_end = t0 + timedelta(minutes=11)
        all_orders += algo.on_market_tick(_tick(t_end))
        assert sum(o.qty for o in all_orders) == 100


# ---------------------------------------------------------------------------
# 3. vol_regime_from_spread helper
# ---------------------------------------------------------------------------

class TestVolRegimeFromSpread:
    def test_import(self):
        from src.execution.twap import vol_regime_from_spread
        assert callable(vol_regime_from_spread)

    def test_tight_spread_is_low(self):
        from src.execution.twap import vol_regime_from_spread
        assert vol_regime_from_spread(spread=0.001) == VolatilityRegime.LOW

    def test_wide_spread_is_high(self):
        from src.execution.twap import vol_regime_from_spread
        assert vol_regime_from_spread(spread=0.05) == VolatilityRegime.HIGH

    def test_mid_spread(self):
        from src.execution.twap import vol_regime_from_spread
        assert vol_regime_from_spread(spread=0.01) == VolatilityRegime.MID

    def test_custom_thresholds(self):
        from src.execution.twap import vol_regime_from_spread
        regime = vol_regime_from_spread(spread=0.008, low_threshold=0.005, high_threshold=0.02)
        assert regime == VolatilityRegime.MID


# ---------------------------------------------------------------------------
# 4. KRX VI gate integration
# ---------------------------------------------------------------------------

class TestKRXVIGateIntegration:
    def test_vi_halted_suspends_twap_ioc(self):
        """During KRX halt, TWAP-generated IOC orders are buffered, not sent."""
        algo = TWAPAlgo(duration=timedelta(minutes=10), slice_count=4)
        handler = KRXSingleAuctionHandler(policy=SingleAuctionPolicy.WAIT)

        t0 = datetime(2024, 1, 1, 9, 0, 0)
        state0 = _state(t0)
        orders = algo.plan(_parent(400), state0)

        # Normal state: filter passes through
        passed = handler.filter(orders, state0)
        assert len(passed) == 1

        # VI triggered: in_single_auction=True
        vi_state = _state(t0, in_single_auction=True)
        t1 = t0 + timedelta(minutes=2, seconds=30)
        tick1 = _tick(t1)
        orders2 = algo.on_market_tick(tick1)
        buffered = handler.filter(orders2, vi_state)
        assert len(buffered) == 0
        assert handler.queued >= 1

    def test_vi_resume_flushes_queue(self):
        """After VI ends, queued orders are released on next continuous trading tick."""
        algo = TWAPAlgo(duration=timedelta(minutes=10), slice_count=4)
        handler = KRXSingleAuctionHandler(policy=SingleAuctionPolicy.WAIT)

        t0 = datetime(2024, 1, 1, 9, 0, 0)
        orders = algo.plan(_parent(400), _state(t0))

        # Buffer during VI
        vi_state = _state(t0, in_single_auction=True)
        handler.filter(orders, vi_state)
        assert handler.queued == 1

        # Resume continuous trading
        t1 = t0 + timedelta(minutes=5)
        resume_state = _state(t1, in_single_auction=False)
        tick2 = _tick(t1)
        orders2 = algo.on_market_tick(tick2)
        flushed = handler.filter(orders2, resume_state)
        # flushed should contain the queued order + any new ones
        assert len(flushed) >= 1
        assert handler.queued == 0

    def test_halted_cancels_with_cancel_policy(self):
        """With CANCEL policy, orders during halt are dropped."""
        handler = KRXSingleAuctionHandler(policy=SingleAuctionPolicy.CANCEL)
        algo = TWAPAlgo(duration=timedelta(minutes=10), slice_count=4)
        t0 = datetime(2024, 1, 1, 9, 0, 0)
        orders = algo.plan(_parent(400), _state(t0))
        halted_state = _state(t0, halted=True)
        result = handler.filter(orders, halted_state)
        assert result == []
        assert handler.queued == 0

    def test_vi_with_vol_adaptive_twap(self):
        """Full integration: vol-adaptive TWAP + KRX VI gate."""
        algo = TWAPAlgo(
            duration=timedelta(minutes=10),
            slice_count=4,
            volatility_weight=[1.5, 1.0, 0.75, 0.75],
        )
        handler = KRXSingleAuctionHandler(policy=SingleAuctionPolicy.WAIT)
        t0 = datetime(2024, 1, 1, 9, 0, 0)

        # plan — normal market
        orders = algo.plan(_parent(400), _state(t0))
        passed = handler.filter(orders, _state(t0))
        assert len(passed) == 1

        # VI fires at t+2min
        t_vi = t0 + timedelta(minutes=2)
        vi_state = _state(t_vi, in_single_auction=True)
        orders2 = algo.on_market_tick(_tick(t_vi))
        buffered = handler.filter(orders2, vi_state)
        assert len(buffered) == 0

        # VI ends at t+4min
        t_resume = t0 + timedelta(minutes=4)
        resume_state = _state(t_resume)
        orders3 = algo.on_market_tick(_tick(t_resume))
        flushed = handler.filter(orders3, resume_state)
        # queued + new orders flushed
        assert len(flushed) >= 1


# ---------------------------------------------------------------------------
# 5. Slippage improvement measurement (simple smoke test)
# ---------------------------------------------------------------------------

class TestSlippageImprovement:
    def test_vi_gate_reduces_orders_during_halt(self):
        """Orders sent during VI with gate = 0; without gate = some."""
        t0 = datetime(2024, 1, 1, 9, 0, 0)
        parent = _parent(400)

        # Without gate
        algo_no_gate = TWAPAlgo(duration=timedelta(minutes=10), slice_count=4)
        algo_no_gate.plan(parent, _state(t0))
        t_vi = t0 + timedelta(minutes=2, seconds=30)
        orders_no_gate = algo_no_gate.on_market_tick(_tick(t_vi))

        # With gate
        algo_gate = TWAPAlgo(duration=timedelta(minutes=10), slice_count=4)
        handler = KRXSingleAuctionHandler(policy=SingleAuctionPolicy.WAIT)
        algo_gate.plan(parent, _state(t0))
        orders_gate_raw = algo_gate.on_market_tick(_tick(t_vi))
        vi_state = _state(t_vi, in_single_auction=True)
        orders_passed = handler.filter(orders_gate_raw, vi_state)

        # Without gate, some orders attempt to go through
        # With gate, they are buffered (0 passed)
        assert len(orders_no_gate) >= 1
        assert len(orders_passed) == 0
