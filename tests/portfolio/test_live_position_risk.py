"""Unit tests for LivePositionRiskManager (#227 S2)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.live.pnl_aggregator import PnLAggregator
from src.live.strategy_position_store import StrategyPositionStore
from src.portfolio.live_position_risk import LivePositionRiskManager, StopTpPolicy


def _setup(
    *,
    sid: str = "live_rsi",
    symbol: str = "005930",
    entry_price: float = 80_000.0,
    qty: float = 100.0,
    stop_loss_pct: float = 0.03,
    take_profit_pct: float = 0.06,
    trailing_stop_pct: float | None = None,
) -> tuple[LivePositionRiskManager, list[Any]]:
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    pnl.record_fill(
        strategy_id=sid,
        symbol=symbol,
        side="buy",
        qty=Decimal(str(qty)),
        price=Decimal(str(entry_price)),
    )
    store.record_fill(
        strategy_id=sid,
        symbol=symbol,
        side="buy",
        qty=Decimal(str(qty)),
    )
    captured_events: list[Any] = []
    mgr = LivePositionRiskManager(
        position_store=store,
        pnl_aggregator=pnl,
        wal_observer=captured_events.append,
    )
    mgr.register_strategy_policy(
        sid,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trailing_stop_pct=trailing_stop_pct,
    )
    return mgr, captured_events


def _now() -> datetime:
    return datetime(2026, 5, 11, 14, 30, tzinfo=timezone.utc)


class TestStopTpPolicy:
    def test_valid_policy(self):
        p = StopTpPolicy(stop_loss_pct=0.03, take_profit_pct=0.06)
        assert p.stop_loss_pct == 0.03
        assert p.trailing_stop_pct is None

    def test_invalid_stop_loss_raises(self):
        with pytest.raises(ValueError):
            StopTpPolicy(stop_loss_pct=0.0, take_profit_pct=0.06)
        with pytest.raises(ValueError):
            StopTpPolicy(stop_loss_pct=1.5, take_profit_pct=0.06)

    def test_invalid_trailing_raises(self):
        with pytest.raises(ValueError):
            StopTpPolicy(
                stop_loss_pct=0.03, take_profit_pct=0.06, trailing_stop_pct=0.0,
            )


class TestStopLoss:
    def test_stop_loss_triggers_at_threshold(self):
        mgr, events = _setup()
        # 80,000 * (1 - 0.03) = 77,600 — last_price <= 77,600 should trigger
        intents = mgr.evaluate("005930", Decimal("76400"), _now())
        assert len(intents) == 1
        intent = intents[0]
        assert intent.side == "sell"
        assert intent.symbol == "005930"
        assert intent.strategy_id == "live_rsi"
        assert intent.qty == 100.0
        assert "live_stop_loss" in intent.reason
        assert "entry=80000" in intent.reason
        assert len(events) == 1
        assert events[0].event_type == "position_stop_triggered"
        assert events[0].payload["trigger"] == "stop_loss"

    def test_stop_loss_does_not_trigger_above_threshold(self):
        mgr, events = _setup()
        # 78,000 > 77,600 → no trigger
        intents = mgr.evaluate("005930", Decimal("78000"), _now())
        assert intents == []
        assert events == []

    def test_stop_loss_at_exact_threshold_triggers(self):
        mgr, _ = _setup()
        intents = mgr.evaluate("005930", Decimal("77600"), _now())  # exactly threshold
        assert len(intents) == 1


class TestTakeProfit:
    def test_take_profit_triggers(self):
        mgr, events = _setup()
        # 80,000 * (1 + 0.06) = 84,800
        intents = mgr.evaluate("005930", Decimal("84800"), _now())
        assert len(intents) == 1
        assert intents[0].side == "sell"
        assert "live_take_profit" in intents[0].reason
        assert events[0].payload["trigger"] == "take_profit"

    def test_take_profit_does_not_trigger_below(self):
        mgr, _ = _setup()
        intents = mgr.evaluate("005930", Decimal("82000"), _now())
        assert intents == []


class TestTrailingStop:
    def test_trailing_stop_triggers_after_high_water(self):
        # Wide take_profit so the high-water tick alone doesn't fire TP first.
        mgr, events = _setup(take_profit_pct=0.30, trailing_stop_pct=0.02)
        # Tick 1 — price climbs to 90,000 (above entry, sets high water).
        # 90000/80000 = +12.5% < take_profit_pct=30% → no TP, no trailing fire.
        assert mgr.evaluate("005930", Decimal("90000"), _now()) == []
        # Tick 2 — price drops to 88,200 (= 90000 * (1 - 0.02)) → trailing fires.
        intents = mgr.evaluate("005930", Decimal("88200"), _now())
        assert len(intents) == 1
        assert "live_trailing_stop" in intents[0].reason
        assert events[0].payload["trigger"] == "trailing_stop"

    def test_trailing_stop_does_not_fire_before_breaking_above_entry(self):
        """Trailing should not fire while price stays at-or-below entry —
        stop_loss is responsible for that range."""
        mgr, _ = _setup(stop_loss_pct=0.05, trailing_stop_pct=0.02)
        # Price never crosses entry, drops a bit but well above stop_loss.
        intents = mgr.evaluate("005930", Decimal("79500"), _now())
        # 79500 > 76000 (entry * 0.95) → stop_loss no
        # high_water still avg_cost (80000), 80000*0.98=78400; 79500 > 78400 → no
        assert intents == []

    def test_trailing_resets_after_sell(self):
        mgr, _ = _setup(take_profit_pct=0.30, trailing_stop_pct=0.02)
        # First entry: ride up + trailing fires
        mgr.evaluate("005930", Decimal("90000"), _now())
        intents = mgr.evaluate("005930", Decimal("88200"), _now())
        assert len(intents) == 1
        # The store still says held=100 (the SELL is an intent, not a fill).
        # But a subsequent evaluate at 88,300 (above last trail) should NOT
        # re-fire if high_water was reset to entry. To assert reset, we need
        # to reset position too — emulate post-fill: store records sell.
        store: StrategyPositionStore = mgr._position_store
        store.record_fill(
            strategy_id="live_rsi", symbol="005930", side="sell", qty=Decimal("100"),
        )
        # After flat, evaluate should return [] and clear stale high_water.
        assert mgr.evaluate("005930", Decimal("88300"), _now()) == []
        assert ("live_rsi", "005930") not in mgr._high_water


class TestNoPosition:
    def test_no_position_returns_empty(self):
        store = StrategyPositionStore()
        pnl = PnLAggregator()
        mgr = LivePositionRiskManager(position_store=store, pnl_aggregator=pnl)
        mgr.register_strategy_policy("live_rsi", stop_loss_pct=0.03, take_profit_pct=0.06)
        intents = mgr.evaluate("005930", Decimal("70000"), _now())
        assert intents == []

    def test_unregistered_strategy_no_emit(self):
        mgr, _ = _setup()
        # Symbol exists, but only 'live_rsi' policy is registered. evaluate
        # for a symbol held by that strategy still works:
        intents = mgr.evaluate("005930", Decimal("76400"), _now())
        assert len(intents) == 1
        # If we evaluate a symbol nobody holds → empty.
        intents2 = mgr.evaluate("999999", Decimal("76400"), _now())
        assert intents2 == []


class TestMultipleStrategies:
    def test_two_strategies_independent_thresholds(self):
        store = StrategyPositionStore()
        pnl = PnLAggregator()
        for sid in ("strat_tight", "strat_loose"):
            pnl.record_fill(
                strategy_id=sid, symbol="005930", side="buy",
                qty=Decimal("50"), price=Decimal("80000"),
            )
            store.record_fill(
                strategy_id=sid, symbol="005930", side="buy", qty=Decimal("50"),
            )
        mgr = LivePositionRiskManager(position_store=store, pnl_aggregator=pnl)
        mgr.register_strategy_policy("strat_tight", stop_loss_pct=0.02, take_profit_pct=0.04)
        mgr.register_strategy_policy("strat_loose", stop_loss_pct=0.10, take_profit_pct=0.20)

        # Price at -3% — only the tight strategy's stop should fire.
        intents = mgr.evaluate("005930", Decimal("77600"), _now())
        assert len(intents) == 1
        assert intents[0].strategy_id == "strat_tight"
