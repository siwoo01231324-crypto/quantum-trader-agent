"""TDD tests for MockMatchingEngine partial fill support (#110)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.brokers.base import OrderRequest, OrderType
from src.execution.base import MarketState, Side, Tick, TimeInForce
from src.execution.mock_matching import MockMatchingEngine


def _tick(bid: float = 99.0, ask: float = 101.0, last: float = 100.0, volume: int = 1000) -> Tick:
    return Tick(symbol="BTCUSDT", bid=bid, ask=ask, last=last, volume=volume, ts=datetime.now(timezone.utc))


def _market_state(adv: float = 100.0, **tick_kwargs) -> MarketState:
    return MarketState(tick=_tick(**tick_kwargs), adv=adv)


def _order(
    side: Side = Side.BUY,
    qty: str = "1",
    order_type: OrderType = OrderType.MARKET,
    price: str | None = None,
    client_order_id: str = "cid-001",
) -> OrderRequest:
    return OrderRequest(
        client_order_id=client_order_id,
        symbol="BTCUSDT",
        side=side,
        qty=Decimal(qty),
        order_type=order_type,
        price=Decimal(price) if price is not None else None,
        tif=TimeInForce.GTC,
    )


# ---------------------------------------------------------------------------
# partial_fill_enabled=False (Phase 1 default — regression guard)
# ---------------------------------------------------------------------------

class TestPartialFillDisabled:
    def test_default_disabled(self):
        engine = MockMatchingEngine()
        assert engine.partial_fill_enabled is False

    def test_large_order_fills_100pct_when_disabled(self):
        engine = MockMatchingEngine(partial_fill_enabled=False)
        state = _market_state(adv=10.0)  # order qty >> adv
        fills = engine.match(_order(qty="100"), state)
        assert len(fills) == 1
        assert fills[0].qty == Decimal("100")

    def test_seed_param_ignored_when_disabled(self):
        engine = MockMatchingEngine(partial_fill_enabled=False, seed=42)
        state = _market_state(adv=10.0)
        fills = engine.match(_order(qty="50"), state)
        assert len(fills) == 1
        assert fills[0].qty == Decimal("50")


# ---------------------------------------------------------------------------
# partial_fill_enabled=True — basic behavior
# ---------------------------------------------------------------------------

class TestPartialFillEnabled:
    def test_small_order_fills_100pct(self):
        """Order qty much smaller than ADV → single 100% fill."""
        engine = MockMatchingEngine(partial_fill_enabled=True, seed=0)
        # qty=1, adv=1000 → ratio=0.001, essentially full fill
        state = _market_state(adv=1000.0)
        fills = engine.match(_order(qty="1"), state)
        total_qty = sum(f.qty for f in fills)
        assert total_qty == Decimal("1")

    def test_large_order_produces_multiple_fills(self):
        """Order qty >> ADV → multiple partial fills."""
        engine = MockMatchingEngine(partial_fill_enabled=True, seed=42)
        # qty=50, adv=10 → ratio=5.0 → should produce multiple fills
        state = _market_state(adv=10.0)
        fills = engine.match(_order(qty="50"), state)
        assert len(fills) > 1

    def test_total_fill_qty_equals_order_qty(self):
        """All partial fills sum to full order qty."""
        engine = MockMatchingEngine(partial_fill_enabled=True, seed=7)
        state = _market_state(adv=10.0)
        fills = engine.match(_order(qty="50"), state)
        total = sum(f.qty for f in fills)
        assert total == Decimal("50")

    def test_each_fill_has_unique_trade_id(self):
        """Each partial fill gets its own trade_id."""
        engine = MockMatchingEngine(partial_fill_enabled=True, seed=42)
        state = _market_state(adv=10.0)
        fills = engine.match(_order(qty="50"), state)
        trade_ids = [f.trade_id for f in fills]
        assert len(trade_ids) == len(set(trade_ids))

    def test_each_fill_references_same_client_order_id(self):
        """All fills share the same client_order_id (idempotency)."""
        engine = MockMatchingEngine(partial_fill_enabled=True, seed=42)
        state = _market_state(adv=10.0)
        fills = engine.match(_order(qty="50", client_order_id="cid-xyz"), state)
        assert all(f.client_order_id == "cid-xyz" for f in fills)

    def test_each_fill_qty_is_positive(self):
        """All fill quantities must be positive."""
        engine = MockMatchingEngine(partial_fill_enabled=True, seed=99)
        state = _market_state(adv=5.0)
        fills = engine.match(_order(qty="30"), state)
        assert all(f.qty > Decimal("0") for f in fills)

    def test_fill_price_is_correct(self):
        """Fill price reflects market mid."""
        engine = MockMatchingEngine(partial_fill_enabled=True, seed=0)
        state = _market_state(adv=10.0, last=100.0)
        fills = engine.match(_order(qty="50"), state)
        assert all(f.price == Decimal("100") for f in fills)

    def test_fee_per_fill_is_correct(self):
        """Each fill's fee = qty * price * taker_fee_bps / 10000."""
        engine = MockMatchingEngine(partial_fill_enabled=True, seed=42)
        state = _market_state(adv=10.0, last=100.0)
        fills = engine.match(_order(qty="50"), state)
        for fill in fills:
            expected_fee = (fill.qty * fill.price * Decimal("5") / Decimal("10000")).quantize(
                Decimal("0.00000001")
            )
            assert fill.fee == expected_fee

    def test_adv_zero_falls_back_to_full_fill(self):
        """ADV=0 edge case → single 100% fill (avoid division by zero)."""
        engine = MockMatchingEngine(partial_fill_enabled=True, seed=0)
        state = _market_state(adv=0.0)
        fills = engine.match(_order(qty="10"), state)
        assert len(fills) == 1
        assert fills[0].qty == Decimal("10")


# ---------------------------------------------------------------------------
# Deterministic reproducibility
# ---------------------------------------------------------------------------

class TestDeterministicSeed:
    def test_same_seed_same_fills(self):
        """Same seed produces identical fill sequences."""
        state = _market_state(adv=10.0)
        order = _order(qty="50")

        engine_a = MockMatchingEngine(partial_fill_enabled=True, seed=42)
        fills_a = engine_a.match(order, state)

        engine_b = MockMatchingEngine(partial_fill_enabled=True, seed=42)
        fills_b = engine_b.match(order, state)

        assert len(fills_a) == len(fills_b)
        for fa, fb in zip(fills_a, fills_b):
            assert fa.qty == fb.qty

    def test_different_seeds_may_differ(self):
        """Different seeds produce different fill sequences (with high probability)."""
        state = _market_state(adv=10.0)
        order = _order(qty="50")

        engine_a = MockMatchingEngine(partial_fill_enabled=True, seed=1)
        fills_a = engine_a.match(order, state)

        engine_b = MockMatchingEngine(partial_fill_enabled=True, seed=9999)
        fills_b = engine_b.match(order, state)

        qtys_a = [f.qty for f in fills_a]
        qtys_b = [f.qty for f in fills_b]
        # Different seeds should produce different results (at least count or quantities differ)
        assert len(qtys_a) != len(qtys_b) or qtys_a != qtys_b

    def test_sequential_calls_advance_rng(self):
        """Successive match() calls on same engine are not identical (RNG advances)."""
        engine = MockMatchingEngine(partial_fill_enabled=True, seed=42)
        state = _market_state(adv=10.0)
        order = _order(qty="50")

        fills_first = engine.match(order, state)
        fills_second = engine.match(order, state)

        qtys_first = [f.qty for f in fills_first]
        qtys_second = [f.qty for f in fills_second]
        # They may differ in count or individual quantities
        assert qtys_first != qtys_second or len(fills_first) != len(fills_second)


# ---------------------------------------------------------------------------
# Limit order partial fill
# ---------------------------------------------------------------------------

class TestLimitOrderPartialFill:
    def test_limit_buy_crossable_partial(self):
        """Limit BUY at/above ask → partial fills when enabled."""
        engine = MockMatchingEngine(partial_fill_enabled=True, seed=42)
        state = _market_state(adv=10.0, bid=99.0, ask=101.0, last=100.0)
        fills = engine.match(
            _order(Side.BUY, qty="50", order_type=OrderType.LIMIT, price="101"),
            state,
        )
        assert len(fills) >= 1
        assert sum(f.qty for f in fills) == Decimal("50")

    def test_limit_buy_not_crossable_returns_empty(self):
        """Limit BUY below ask → empty fills (price miss, no partial)."""
        engine = MockMatchingEngine(partial_fill_enabled=True, seed=42)
        state = _market_state(adv=10.0, bid=99.0, ask=101.0, last=100.0)
        fills = engine.match(
            _order(Side.BUY, qty="50", order_type=OrderType.LIMIT, price="100"),
            state,
        )
        assert fills == []


# ---------------------------------------------------------------------------
# PaperBroker integration — partial fill sequence
# ---------------------------------------------------------------------------

class TestPaperBrokerPartialFillIntegration:
    """Integration test: PaperBroker handles partial fill sequences correctly."""

    @pytest.mark.asyncio
    async def test_partial_fill_wal_events_sum_to_order_qty(self, tmp_path):
        """Large order → multiple WAL order_filled events, fill_qty sum == order.qty."""
        from src.execution.paper_broker import PaperBroker
        from src.live.wal import WAL, replay
        from src.ops.kill_switch import KillSwitch

        wal_path = tmp_path / "test.wal"
        wal = WAL(wal_path)
        ks = KillSwitch()
        engine = MockMatchingEngine(partial_fill_enabled=True, seed=42)
        broker = PaperBroker(
            wal=wal,
            kill_switch=ks,
            matching_engine=engine,
            initial_balance=Decimal("1000000"),
        )

        from src.execution.base import Tick
        from datetime import datetime, timezone
        state = MarketState(
            tick=Tick(
                symbol="BTCUSDT", bid=99.0, ask=101.0, last=100.0,
                volume=1000, ts=datetime.now(timezone.utc)
            ),
            adv=10.0,
        )
        broker.update_market(state)

        req = _order(qty="50", client_order_id="integration-001")
        ack = await broker.place_order(req)

        # All fills enqueued — drain queue
        from src.brokers.types import BrokerFill
        fills: list[BrokerFill] = []
        while not broker._fills_queue.empty():
            fills.append(broker._fills_queue.get_nowait())

        total_filled = sum(f.qty for f in fills)
        assert total_filled == Decimal("50")

        # WAL events: count of order_filled should match number of fills
        events, _ = replay(wal_path)
        filled_events = [e for e in events if e.event_type == "order_filled"]
        assert len(filled_events) == len(fills)

    @pytest.mark.asyncio
    async def test_partial_fill_ack_status_filled(self, tmp_path):
        """Ack status is FILLED even for partial fill sequences."""
        from src.execution.paper_broker import PaperBroker
        from src.live.wal import WAL
        from src.live.types import OrderStatus
        from src.ops.kill_switch import KillSwitch

        wal_path = tmp_path / "test2.wal"
        wal = WAL(wal_path)
        ks = KillSwitch()
        engine = MockMatchingEngine(partial_fill_enabled=True, seed=42)
        broker = PaperBroker(
            wal=wal,
            kill_switch=ks,
            matching_engine=engine,
            initial_balance=Decimal("1000000"),
        )

        from src.execution.base import Tick
        from datetime import datetime, timezone
        state = MarketState(
            tick=Tick(
                symbol="BTCUSDT", bid=99.0, ask=101.0, last=100.0,
                volume=1000, ts=datetime.now(timezone.utc)
            ),
            adv=10.0,
        )
        broker.update_market(state)

        req = _order(qty="50", client_order_id="integration-002")
        ack = await broker.place_order(req)
        assert ack.status == OrderStatus.FILLED.value
        assert ack.qty == Decimal("50")
