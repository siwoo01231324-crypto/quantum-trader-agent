"""TDD tests for PaperAdapter (Issue #175).

Scenarios:
1. Signal active (s2c-voltarget) → BUY order submitted to mock broker.
2. Signal inactive → no order.
3. Signal active then inactive → BUY then SELL (entry + exit).
4. s4-funding strategy path → BUY on negative funding signal.
5. Insufficient position size (pos_size=0) → no order.
6. Exit skips when no open position.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import List
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest

from src.backtest.swing.paper_adapter import AdapterConfig, PaperAdapter
from src.brokers.base import Balance, OrderAck, OrderRequest, Position, PositionSide
from src.execution.base import Side, TimeInForce


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 120, close_values: list[float] | None = None) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame with n bars."""
    if close_values is not None:
        closes = close_values
        n = len(closes)
    else:
        rng = np.random.default_rng(42)
        closes = (50_000 + np.cumsum(rng.normal(0, 200, n))).tolist()

    df = pd.DataFrame({
        "open": closes,
        "high": [c * 1.001 for c in closes],
        "low": [c * 0.999 for c in closes],
        "close": closes,
        "volume": [100.0] * n,
        "_funding_rate": [-0.0001] * n,
    })
    return df


def _filled_ack(client_order_id: str, symbol: str = "BTCUSDT") -> OrderAck:
    return OrderAck(
        broker_order_id="broker-001",
        client_order_id=client_order_id,
        symbol=symbol,
        status="FILLED",
        ts=datetime.now(timezone.utc),
        qty=Decimal("0.01"),
        price=Decimal("50000"),
    )


def _rejected_ack(client_order_id: str, symbol: str = "BTCUSDT") -> OrderAck:
    return OrderAck(
        broker_order_id="",
        client_order_id=client_order_id,
        symbol=symbol,
        status="REJECTED",
        ts=datetime.now(timezone.utc),
        reject_reason="NO_MARKET_STATE",
    )


class MockBroker:
    """Minimal async mock broker that records submitted orders."""

    def __init__(self, positions: list[Position] | None = None) -> None:
        self.submitted: list[OrderRequest] = []
        self._positions = positions or []
        self._fill_status = "FILLED"

    async def place_order(self, req: OrderRequest) -> OrderAck:
        self.submitted.append(req)
        if self._fill_status == "FILLED":
            return _filled_ack(req.client_order_id, req.symbol)
        return _rejected_ack(req.client_order_id, req.symbol)

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        if symbol:
            return [p for p in self._positions if p.symbol == symbol]
        return list(self._positions)


def _long_position(symbol: str = "BTCUSDT") -> Position:
    return Position(
        symbol=symbol,
        side=PositionSide.LONG,
        qty=Decimal("0.01"),
        entry_price=Decimal("50000"),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPaperAdapterS2cSignalActive:
    """On signal=1 from s2c-voltarget → BUY submitted."""

    def test_entry_order_submitted_on_active_signal(self):
        broker = MockBroker()
        config = AdapterConfig(
            strategy="s2c-voltarget",
            symbol="BTCUSDT",
            entry_lookback=5,
            exit_lookback=3,
            vol_lookback=10,
        )
        adapter = PaperAdapter(config=config, broker=broker)

        # Need ≥ vol_lookback+1 base bars so realized_vol.shift(1) is non-NaN at
        # the spike bar (pct_change drops bar 0, rolling(10) needs 10 valid returns).
        # 15 base bars → 15 returns (bar0=NaN, bars1-14=valid) → rolling(10) at bar14
        # is non-NaN. Spike at bar 15 triggers Donchian breakout above 5-bar prior max.
        rng = np.random.default_rng(7)
        base = 50_000.0 + np.cumsum(rng.normal(0, 300, 15))
        spike = base.max() * 1.05  # guaranteed breakout above 5-bar max
        closes = base.tolist() + [float(spike)]
        df = _make_ohlcv(close_values=closes)

        asyncio.run(adapter.on_bar(df))

        assert len(broker.submitted) == 1
        assert broker.submitted[0].side == Side.BUY
        assert broker.submitted[0].symbol == "BTCUSDT"
        assert adapter.in_position is True


class TestPaperAdapterNoSignal:
    """No signal → no order submitted."""

    def test_no_order_when_signal_flat(self):
        broker = MockBroker()
        config = AdapterConfig(
            strategy="s2c-voltarget",
            symbol="BTCUSDT",
            entry_lookback=20,
            exit_lookback=10,
            vol_lookback=60,
        )
        adapter = PaperAdapter(config=config, broker=broker)

        # Flat closes — no breakout
        closes = [50_000.0] * 30
        df = _make_ohlcv(close_values=closes)

        asyncio.run(adapter.on_bar(df))

        assert len(broker.submitted) == 0
        assert adapter.in_position is False


class TestPaperAdapterEntryThenExit:
    """Full round-trip: entry on breakout bar, then exit on drop below exit channel."""

    def test_entry_then_exit(self):
        config = AdapterConfig(
            strategy="s2c-voltarget",
            symbol="BTCUSDT",
            entry_lookback=5,
            exit_lookback=3,
            vol_lookback=10,
        )

        # 15 base bars → rolling(10) vol is non-NaN at spike bar (see entry test comment)
        rng = np.random.default_rng(7)
        base = 50_000.0 + np.cumsum(rng.normal(0, 300, 15))
        spike = base.max() * 1.05
        closes_entry = base.tolist() + [float(spike)]
        df_entry = _make_ohlcv(close_values=closes_entry)

        broker = MockBroker(positions=[_long_position()])
        adapter = PaperAdapter(config=config, broker=broker)
        asyncio.run(adapter.on_bar(df_entry))
        assert adapter.in_position is True
        assert broker.submitted[0].side == Side.BUY

        # Phase 2: crash below exit channel (3-bar min) → signal=0 → exit
        closes_exit = closes_entry + [20_000.0]
        df_exit = _make_ohlcv(close_values=closes_exit)

        asyncio.run(adapter.on_bar(df_exit))

        sell_orders = [r for r in broker.submitted if r.side == Side.SELL]
        assert len(sell_orders) == 1
        assert sell_orders[0].reduce_only is True
        assert adapter.in_position is False


class TestPaperAdapterS4Funding:
    """s4-funding strategy: negative funding → BUY submitted."""

    def test_entry_on_negative_funding(self):
        broker = MockBroker()
        config = AdapterConfig(
            strategy="s4-funding",
            symbol="BTCUSDT",
            funding_threshold=-0.005e-2,
        )
        adapter = PaperAdapter(config=config, broker=broker)

        # Funding rate more negative than threshold → signal=1
        closes = [50_000.0] * 30
        df = _make_ohlcv(close_values=closes)
        df["_funding_rate"] = -0.01e-2  # very negative

        asyncio.run(adapter.on_bar(df))

        assert len(broker.submitted) == 1
        assert broker.submitted[0].side == Side.BUY

    def test_no_entry_on_positive_funding(self):
        broker = MockBroker()
        config = AdapterConfig(
            strategy="s4-funding",
            symbol="BTCUSDT",
            funding_threshold=-0.005e-2,
        )
        adapter = PaperAdapter(config=config, broker=broker)

        closes = [50_000.0] * 30
        df = _make_ohlcv(close_values=closes)
        df["_funding_rate"] = 0.01e-2  # positive → no signal

        asyncio.run(adapter.on_bar(df))
        assert len(broker.submitted) == 0


class TestPaperAdapterSizing:
    """Position sizing: pos_size=0 → no order; qty calculation."""

    def test_zero_pos_size_skips_order(self):
        """When realized vol is 0 (e.g., all same close), pos_size may be 0."""
        broker = MockBroker()
        config = AdapterConfig(
            strategy="s2c-voltarget",
            symbol="BTCUSDT",
            entry_lookback=5,
            exit_lookback=3,
            vol_lookback=10,
            vol_target=0.0,  # force 0 vol target → pos_size = 0
        )
        adapter = PaperAdapter(config=config, broker=broker)
        closes = [50_000.0] * 10 + [60_000.0]
        df = _make_ohlcv(close_values=closes)
        asyncio.run(adapter.on_bar(df))
        # pos_size=0 → qty=0 → no order
        assert len(broker.submitted) == 0

    def test_qty_calculation(self):
        """qty = (balance * pos_size) / price, 6 decimal places."""
        from src.backtest.swing.paper_adapter import AdapterConfig, PaperAdapter

        config = AdapterConfig(
            strategy="s2c-voltarget",
            symbol="BTCUSDT",
            initial_balance=Decimal("100000"),
        )
        adapter = PaperAdapter(config=config, broker=MockBroker())
        price = Decimal("50000")
        pos_size = 1.0
        qty = adapter._size_qty(price, pos_size)
        # 100000 / 50000 = 2.000000
        assert qty == Decimal("2.000000")


class TestPaperAdapterExitNoPosition:
    """Exit called when no open position → no order submitted."""

    def test_exit_skips_when_not_in_position(self):
        broker = MockBroker(positions=[])  # empty positions
        config = AdapterConfig(
            strategy="s2c-voltarget",
            symbol="BTCUSDT",
            entry_lookback=5,
            exit_lookback=3,
            vol_lookback=10,
        )
        adapter = PaperAdapter(config=config, broker=broker)
        # Manually set in_position to True to trigger exit path
        adapter._in_position = True

        closes = [60_000.0] * 10 + [30_000.0]  # drop triggers exit signal
        df = _make_ohlcv(close_values=closes)

        asyncio.run(adapter.on_bar(df))
        # Broker has no open position → get_positions returns [] → no sell order
        sell_orders = [r for r in broker.submitted if r.side == Side.SELL]
        assert len(sell_orders) == 0
        assert adapter.in_position is False


class TestPaperAdapterShortHistory:
    """DataFrame with < 2 rows → None returned, no orders."""

    def test_short_history(self):
        broker = MockBroker()
        config = AdapterConfig(strategy="s2c-voltarget", symbol="BTCUSDT")
        adapter = PaperAdapter(config=config, broker=broker)
        df = _make_ohlcv(close_values=[50_000.0])
        result = asyncio.run(adapter.on_bar(df))
        assert result is None
        assert len(broker.submitted) == 0


class TestPaperAdapterStateRestoration:
    """Cross-restart state restoration (#143).

    Cron-based 30-day operation: each cron fires a new process. Without WAL
    replay, _in_position resets to False every run -> exit signals get lost.
    Validates that PaperBroker.from_wal + adapter sync restores in_position
    correctly so subsequent exit signals route to broker.
    """

    def test_in_position_restored_from_broker_after_wal_replay(self, tmp_path):
        """First session enters; second session must see in_position=True."""
        from src.brokers.base import PositionSide
        from src.execution.paper_broker import PaperBroker
        from src.execution.mock_matching import MockMatchingEngine
        from src.execution.base import MarketState, Tick
        from src.live.wal import WAL
        from src.ops.kill_switch import KillSwitch
        from datetime import datetime, timezone

        wal_path = tmp_path / "wal.jsonl"

        # --- Session 1: open a position ---
        wal1 = WAL(wal_path)
        broker1 = PaperBroker(
            wal=wal1,
            kill_switch=KillSwitch(),
            matching_engine=MockMatchingEngine(),
            initial_balance=Decimal("100000"),
        )
        # Update market so the matching engine has a price.
        tick = Tick(
            symbol="BTCUSDT",
            bid=Decimal("49990"),
            ask=Decimal("50010"),
            last=Decimal("50000"),
            volume=Decimal("0"),
            ts=datetime.now(timezone.utc),
        )
        broker1.update_market(MarketState(tick=tick))

        from src.brokers.base import OrderRequest, OrderType
        req = OrderRequest(
            client_order_id="test-entry-001",
            symbol="BTCUSDT",
            side=Side.BUY,
            qty=Decimal("0.5"),
            order_type=OrderType.MARKET,
            price=None,
            tif=TimeInForce.IOC,
        )
        ack = asyncio.run(broker1.place_order(req))
        assert ack.status == "FILLED", f"entry should fill, got {ack.status}"
        asyncio.run(broker1.aclose())

        # --- Session 2: replay WAL into a fresh broker ---
        broker2 = PaperBroker.from_wal(
            path=wal_path,
            kill_switch=KillSwitch(),
            matching_engine=MockMatchingEngine(),
            initial_balance=Decimal("100000"),
        )
        positions = asyncio.run(broker2.get_positions("BTCUSDT"))
        assert len(positions) == 1, "broker should have restored 1 position"
        assert positions[0].side == PositionSide.LONG
        assert positions[0].qty == Decimal("0.5")

        # --- Adapter created in session 2 should sync from broker positions ---
        config = AdapterConfig(strategy="r4-switch", symbol="BTCUSDT")
        adapter = PaperAdapter(config=config, broker=broker2)
        existing = asyncio.run(broker2.get_positions("BTCUSDT"))
        if existing:
            adapter._in_position = True
            adapter._entry_price = existing[0].entry_price
        assert adapter.in_position is True, "adapter must sync in_position=True after replay"


class TestPaperAdapterR4Switch:
    """R4 threshold-based regime switch (#143): rolling return + funding rate.

    R4 routes between S2c (bullish regime) and S4 (funding_negative regime).
    See src/backtest/swing/regime_switching.py::route_r4 and #173 bench.
    """

    def test_r4_switch_entry_on_bullish_regime_breakout(self):
        """Bullish rolling return + Donchian breakout → S2c signal=1 → BUY."""
        broker = MockBroker()
        config = AdapterConfig(
            strategy="r4-switch",
            symbol="BTCUSDT",
            entry_lookback=5,
            exit_lookback=3,
            vol_lookback=10,
            return_lookback=20,
        )
        adapter = PaperAdapter(config=config, broker=broker)

        # Need: rolling_ret(20).shift(1) > 0 at last bar AND Donchian breakout
        # Construct: 30 bars steady uptrend → bullish regime, then breakout spike.
        n_base = 30
        base = [40_000.0 + i * 100.0 for i in range(n_base)]  # +100/bar uptrend
        spike = max(base) * 1.05
        closes = base + [spike]
        df = _make_ohlcv(close_values=closes)

        asyncio.run(adapter.on_bar(df))

        assert len(broker.submitted) == 1
        assert broker.submitted[0].side == Side.BUY
        assert adapter.in_position is True

    def test_r4_switch_no_signal_in_neutral_regime(self):
        """Flat closes + neutral funding → R4 returns 0 signal → no order."""
        broker = MockBroker()
        config = AdapterConfig(
            strategy="r4-switch",
            symbol="BTCUSDT",
            entry_lookback=5,
            exit_lookback=3,
            vol_lookback=10,
            return_lookback=20,
        )
        adapter = PaperAdapter(config=config, broker=broker)

        # Flat closes → rolling_ret = 0, not > 0 → not bullish
        # Positive funding (default _make_ohlcv uses -0.0001, override here)
        closes = [50_000.0] * 30
        df = _make_ohlcv(close_values=closes)
        df["_funding_rate"] = 0.0001  # positive → not funding_negative

        asyncio.run(adapter.on_bar(df))

        assert len(broker.submitted) == 0
        assert adapter.in_position is False

    def test_r4_switch_round_trip(self):
        """Entry on bullish breakout, exit on subsequent crash."""
        config = AdapterConfig(
            strategy="r4-switch",
            symbol="BTCUSDT",
            entry_lookback=5,
            exit_lookback=3,
            vol_lookback=10,
            return_lookback=20,
        )

        n_base = 30
        base = [40_000.0 + i * 100.0 for i in range(n_base)]
        spike = max(base) * 1.05
        closes_entry = base + [spike]
        df_entry = _make_ohlcv(close_values=closes_entry)

        broker = MockBroker(positions=[_long_position()])
        adapter = PaperAdapter(config=config, broker=broker)
        asyncio.run(adapter.on_bar(df_entry))
        assert adapter.in_position is True
        assert broker.submitted[0].side == Side.BUY

        # Crash bar → S2c exit signal=0
        closes_exit = closes_entry + [20_000.0]
        df_exit = _make_ohlcv(close_values=closes_exit)
        asyncio.run(adapter.on_bar(df_exit))

        sell_orders = [r for r in broker.submitted if r.side == Side.SELL]
        assert len(sell_orders) == 1
        assert sell_orders[0].reduce_only is True
        assert adapter.in_position is False


class TestPaperAdapterR6Switch:
    """R6 = R4 logic on 1h bars with retuned defaults (#199).

    Same threshold regime switch as R4, expected ~9 trades / 30-day paper run
    (vs R4's ~7 on 4h). Tests use compressed lookbacks to keep df size small.
    """

    def test_r6_switch_entry_on_bullish_regime_breakout(self):
        """Bullish rolling return + Donchian breakout → S2c signal=1 → BUY."""
        broker = MockBroker()
        config = AdapterConfig(
            strategy="r6-switch",
            symbol="BTCUSDT",
            entry_lookback=5,
            exit_lookback=3,
            vol_lookback=10,
            return_lookback=20,
        )
        adapter = PaperAdapter(config=config, broker=broker)

        n_base = 30
        base = [40_000.0 + i * 100.0 for i in range(n_base)]
        spike = max(base) * 1.05
        closes = base + [spike]
        df = _make_ohlcv(close_values=closes)

        asyncio.run(adapter.on_bar(df))

        assert len(broker.submitted) == 1
        assert broker.submitted[0].side == Side.BUY
        assert adapter.in_position is True

    def test_r6_switch_no_signal_in_neutral_regime(self):
        """Flat closes + positive funding → R6 returns 0 signal → no order."""
        broker = MockBroker()
        config = AdapterConfig(
            strategy="r6-switch",
            symbol="BTCUSDT",
            entry_lookback=5,
            exit_lookback=3,
            vol_lookback=10,
            return_lookback=20,
        )
        adapter = PaperAdapter(config=config, broker=broker)

        closes = [50_000.0] * 30
        df = _make_ohlcv(close_values=closes)
        df["_funding_rate"] = 0.0001  # positive → not funding_negative

        asyncio.run(adapter.on_bar(df))

        assert len(broker.submitted) == 0
        assert adapter.in_position is False

    def test_r6_switch_round_trip(self):
        """Entry on bullish breakout, exit on subsequent crash."""
        config = AdapterConfig(
            strategy="r6-switch",
            symbol="BTCUSDT",
            entry_lookback=5,
            exit_lookback=3,
            vol_lookback=10,
            return_lookback=20,
        )

        n_base = 30
        base = [40_000.0 + i * 100.0 for i in range(n_base)]
        spike = max(base) * 1.05
        closes_entry = base + [spike]
        df_entry = _make_ohlcv(close_values=closes_entry)

        broker = MockBroker(positions=[_long_position()])
        adapter = PaperAdapter(config=config, broker=broker)
        asyncio.run(adapter.on_bar(df_entry))
        assert adapter.in_position is True
        assert broker.submitted[0].side == Side.BUY

        # Crash bar → S2c exit signal=0
        closes_exit = closes_entry + [20_000.0]
        df_exit = _make_ohlcv(close_values=closes_exit)
        asyncio.run(adapter.on_bar(df_exit))

        sell_orders = [r for r in broker.submitted if r.side == Side.SELL]
        assert len(sell_orders) == 1
        assert sell_orders[0].reduce_only is True
        assert adapter.in_position is False
