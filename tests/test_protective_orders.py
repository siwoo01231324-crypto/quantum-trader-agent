"""Tests for src/brokers/protective_orders.py — Phase 3 안전망 (#127).

Covers:
1. ProtectiveOrderConfig validation (positive pcts only)
2. Price computation (LONG/SHORT × stop/take_profit)
3. register_protection — submits SL + TP via broker
4. register_protection — duplicate symbol rejected
5. cancel_protection — cancels both orders
6. cancel_protection — noop for unregistered symbol
7. cancel — broker error swallowed (logs but no raise)
8. sync_from_broker — orphan detection
9. WAL emission on register/cancel/orphan
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.brokers.protective_orders import (
    ProtectiveOrderConfig,
    ProtectiveOrderManager,
    ProtectivePair,
)


# ---------------------------------------------------------------------------
# Mock broker — implements ProtectiveBrokerProtocol
# ---------------------------------------------------------------------------

class MockBroker:
    name = "mock_broker"

    def __init__(self) -> None:
        self.placed: list[dict] = []
        self.cancelled: list[str] = []
        self._next_id = 1
        self._open_orders: list[dict] = []
        self.cancel_should_raise: bool = False

    def place_protective_order(
        self, *, symbol, side, qty, stop_price, kind,
    ) -> str:
        order_id = f"oid-{self._next_id}"
        self._next_id += 1
        self.placed.append({
            "symbol": symbol, "side": side, "qty": qty,
            "stop_price": stop_price, "kind": kind, "order_id": order_id,
        })
        self._open_orders.append({
            "broker_order_id": order_id,
            "symbol": symbol,
            "side": side,
            "type": kind,
            "stop_price": str(stop_price),
        })
        return order_id

    def cancel_protective_order(self, *, symbol, broker_order_id) -> None:
        if self.cancel_should_raise:
            raise RuntimeError("simulated broker cancel failure")
        self.cancelled.append(broker_order_id)
        self._open_orders = [
            o for o in self._open_orders if o["broker_order_id"] != broker_order_id
        ]

    def list_open_protective_orders(self, *, symbol=None) -> list[dict]:
        if symbol is None:
            return list(self._open_orders)
        return [o for o in self._open_orders if o["symbol"] == symbol]


class MockWAL:
    def __init__(self) -> None:
        self.events: list = []

    def write(self, event) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# ProtectiveOrderConfig
# ---------------------------------------------------------------------------

class TestConfig:
    def test_valid_config(self):
        cfg = ProtectiveOrderConfig(
            stop_loss_pct=Decimal("0.02"),
            take_profit_pct=Decimal("0.04"),
        )
        assert cfg.stop_loss_pct == Decimal("0.02")

    def test_zero_stop_loss_rejected(self):
        with pytest.raises(ValueError, match="stop_loss_pct"):
            ProtectiveOrderConfig(
                stop_loss_pct=Decimal("0"),
                take_profit_pct=Decimal("0.04"),
            )

    def test_negative_take_profit_rejected(self):
        with pytest.raises(ValueError, match="take_profit_pct"):
            ProtectiveOrderConfig(
                stop_loss_pct=Decimal("0.02"),
                take_profit_pct=Decimal("-0.01"),
            )


# ---------------------------------------------------------------------------
# Price computation
# ---------------------------------------------------------------------------

class TestPriceComputation:
    def test_long_entry_prices(self):
        # entry 50000, sl 2%, tp 4%, side BUY (long)
        sl, tp, close_side = ProtectiveOrderManager._compute_protection_prices(
            entry_side="BUY",
            entry_price=Decimal("50000"),
            config=ProtectiveOrderConfig(
                stop_loss_pct=Decimal("0.02"),
                take_profit_pct=Decimal("0.04"),
            ),
        )
        assert sl == Decimal("49000.00")  # 50000 × 0.98
        assert tp == Decimal("52000.00")  # 50000 × 1.04
        assert close_side == "SELL"

    def test_short_entry_prices(self):
        # entry 50000, sl 2%, tp 4%, side SELL (short)
        sl, tp, close_side = ProtectiveOrderManager._compute_protection_prices(
            entry_side="SELL",
            entry_price=Decimal("50000"),
            config=ProtectiveOrderConfig(
                stop_loss_pct=Decimal("0.02"),
                take_profit_pct=Decimal("0.04"),
            ),
        )
        assert sl == Decimal("51000.00")  # 50000 × 1.02 (price goes up = loss for short)
        assert tp == Decimal("48000.00")  # 50000 × 0.96 (price goes down = profit for short)
        assert close_side == "BUY"


# ---------------------------------------------------------------------------
# register_protection
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_submits_two_orders(self):
        broker = MockBroker()
        mgr = ProtectiveOrderManager(broker=broker)
        pair = mgr.register_protection(
            symbol="BTCUSDT",
            entry_side="BUY",
            qty=Decimal("0.1"),
            entry_price=Decimal("50000"),
            config=ProtectiveOrderConfig(
                stop_loss_pct=Decimal("0.02"),
                take_profit_pct=Decimal("0.04"),
            ),
        )
        assert len(broker.placed) == 2
        kinds = {p["kind"] for p in broker.placed}
        assert kinds == {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
        # All close orders are SELL (close long)
        for p in broker.placed:
            assert p["side"] == "SELL"
            assert p["qty"] == Decimal("0.1")
        assert pair.symbol == "BTCUSDT"
        assert pair.sl_order_id != pair.tp_order_id

    def test_register_duplicate_symbol_rejected(self):
        broker = MockBroker()
        mgr = ProtectiveOrderManager(broker=broker)
        cfg = ProtectiveOrderConfig(
            stop_loss_pct=Decimal("0.02"),
            take_profit_pct=Decimal("0.04"),
        )
        mgr.register_protection(
            symbol="BTCUSDT", entry_side="BUY", qty=Decimal("0.1"),
            entry_price=Decimal("50000"), config=cfg,
        )
        with pytest.raises(ValueError, match="already registered"):
            mgr.register_protection(
                symbol="BTCUSDT", entry_side="BUY", qty=Decimal("0.1"),
                entry_price=Decimal("50000"), config=cfg,
            )

    def test_register_short_uses_buy_close_side(self):
        broker = MockBroker()
        mgr = ProtectiveOrderManager(broker=broker)
        mgr.register_protection(
            symbol="BTCUSDT", entry_side="SELL", qty=Decimal("0.1"),
            entry_price=Decimal("50000"),
            config=ProtectiveOrderConfig(
                stop_loss_pct=Decimal("0.02"), take_profit_pct=Decimal("0.04"),
            ),
        )
        for p in broker.placed:
            assert p["side"] == "BUY"  # close short = BUY


# ---------------------------------------------------------------------------
# cancel_protection
# ---------------------------------------------------------------------------

class TestCancel:
    def test_cancel_cancels_both(self):
        broker = MockBroker()
        mgr = ProtectiveOrderManager(broker=broker)
        pair = mgr.register_protection(
            symbol="BTCUSDT", entry_side="BUY", qty=Decimal("0.1"),
            entry_price=Decimal("50000"),
            config=ProtectiveOrderConfig(
                stop_loss_pct=Decimal("0.02"), take_profit_pct=Decimal("0.04"),
            ),
        )
        assert pair is not None
        result = mgr.cancel_protection(symbol="BTCUSDT")
        assert result is not None
        assert len(broker.cancelled) == 2
        assert pair.sl_order_id in broker.cancelled
        assert pair.tp_order_id in broker.cancelled
        assert mgr.get_registered("BTCUSDT") is None

    def test_cancel_unknown_symbol_returns_none(self):
        broker = MockBroker()
        mgr = ProtectiveOrderManager(broker=broker)
        result = mgr.cancel_protection(symbol="NEVER")
        assert result is None
        assert broker.cancelled == []

    def test_cancel_swallows_broker_error(self):
        """Broker cancel failure should not propagate — log + remove from manager state."""
        broker = MockBroker()
        broker.cancel_should_raise = True
        mgr = ProtectiveOrderManager(broker=broker)
        mgr.register_protection(
            symbol="BTCUSDT", entry_side="BUY", qty=Decimal("0.1"),
            entry_price=Decimal("50000"),
            config=ProtectiveOrderConfig(
                stop_loss_pct=Decimal("0.02"), take_profit_pct=Decimal("0.04"),
            ),
        )
        # Should NOT raise even though broker.cancel raises.
        result = mgr.cancel_protection(symbol="BTCUSDT")
        assert result is not None
        # Manager state cleared regardless.
        assert mgr.get_registered("BTCUSDT") is None


# ---------------------------------------------------------------------------
# sync_from_broker
# ---------------------------------------------------------------------------

class TestSyncFromBroker:
    def test_sync_no_orphans(self):
        broker = MockBroker()
        mgr = ProtectiveOrderManager(broker=broker)
        mgr.register_protection(
            symbol="BTCUSDT", entry_side="BUY", qty=Decimal("0.1"),
            entry_price=Decimal("50000"),
            config=ProtectiveOrderConfig(
                stop_loss_pct=Decimal("0.02"), take_profit_pct=Decimal("0.04"),
            ),
        )
        result = mgr.sync_from_broker()
        assert result["orphaned"] == []

    def test_sync_finds_orphan(self):
        broker = MockBroker()
        # Pre-existing exchange order not registered with manager (e.g., manager
        # restart after PC down — manager state lost, exchange state intact).
        broker._open_orders.append({
            "broker_order_id": "orphan-99",
            "symbol": "ETHUSDT",
            "side": "SELL",
            "type": "STOP_MARKET",
            "stop_price": "1500",
        })
        mgr = ProtectiveOrderManager(broker=broker)
        result = mgr.sync_from_broker()
        assert len(result["orphaned"]) == 1
        assert result["orphaned"][0]["broker_order_id"] == "orphan-99"

    def test_sync_filters_by_symbol(self):
        broker = MockBroker()
        mgr = ProtectiveOrderManager(broker=broker)
        mgr.register_protection(
            symbol="BTCUSDT", entry_side="BUY", qty=Decimal("0.1"),
            entry_price=Decimal("50000"),
            config=ProtectiveOrderConfig(
                stop_loss_pct=Decimal("0.02"), take_profit_pct=Decimal("0.04"),
            ),
        )
        # Adding ETH orphan — should not appear when filtering by BTCUSDT.
        broker._open_orders.append({
            "broker_order_id": "orphan-eth", "symbol": "ETHUSDT",
            "type": "STOP_MARKET",
        })
        result = mgr.sync_from_broker(symbol="BTCUSDT")
        assert result["orphaned"] == []  # ETH orphan filtered out by symbol


# ---------------------------------------------------------------------------
# WAL emission
# ---------------------------------------------------------------------------

class TestWALEmission:
    def test_register_emits_wal_event(self):
        broker = MockBroker()
        wal = MockWAL()
        mgr = ProtectiveOrderManager(broker=broker, wal=wal)
        mgr.register_protection(
            symbol="BTCUSDT", entry_side="BUY", qty=Decimal("0.1"),
            entry_price=Decimal("50000"),
            config=ProtectiveOrderConfig(
                stop_loss_pct=Decimal("0.02"), take_profit_pct=Decimal("0.04"),
            ),
        )
        register_events = [
            e for e in wal.events if e.event_type == "protective_registered"
        ]
        assert len(register_events) == 1
        payload = register_events[0].payload
        assert payload["symbol"] == "BTCUSDT"
        assert payload["entry_side"] == "BUY"
        assert payload["broker"] == "mock_broker"

    def test_cancel_emits_wal_event(self):
        broker = MockBroker()
        wal = MockWAL()
        mgr = ProtectiveOrderManager(broker=broker, wal=wal)
        mgr.register_protection(
            symbol="BTCUSDT", entry_side="BUY", qty=Decimal("0.1"),
            entry_price=Decimal("50000"),
            config=ProtectiveOrderConfig(
                stop_loss_pct=Decimal("0.02"), take_profit_pct=Decimal("0.04"),
            ),
        )
        wal.events.clear()
        mgr.cancel_protection(symbol="BTCUSDT")
        cancel_events = [e for e in wal.events if e.event_type == "protective_cancelled"]
        assert len(cancel_events) == 1

    def test_sync_emits_orphan_events(self):
        broker = MockBroker()
        broker._open_orders.append({
            "broker_order_id": "orphan-1", "symbol": "BTCUSDT",
            "type": "STOP_MARKET",
        })
        wal = MockWAL()
        mgr = ProtectiveOrderManager(broker=broker, wal=wal)
        mgr.sync_from_broker()
        orphan_events = [e for e in wal.events if e.event_type == "protective_orphaned"]
        assert len(orphan_events) == 1
