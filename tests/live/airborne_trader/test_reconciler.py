"""Unit tests for AirborneTrader.reconcile_on_startup."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from live.airborne_fire_listener import AirborneFireListener
from live.airborne_trader.config import AirborneTraderConfig
from live.airborne_trader.risk import AirborneTraderRisk
from live.airborne_trader.state import AirborneTraderState
from live.airborne_trader.trader import AirborneTrader, DummyBroker


def _make_trader(tmp_path) -> AirborneTrader:
    state = AirborneTraderState(path=tmp_path / "state.db")
    config = AirborneTraderConfig()
    risk = AirborneTraderRisk(config, state)
    listener = AirborneFireListener()
    broker = DummyBroker()
    fixed_now = datetime(2026, 5, 27, 2, 1, tzinfo=timezone.utc)
    return AirborneTrader(
        config=config, state=state, risk=risk,
        listener=listener, broker=broker,
        now_provider=lambda: fixed_now,
    )


@pytest.mark.asyncio
async def test_reconciler_no_open_positions(tmp_path):
    trader = _make_trader(tmp_path)
    try:
        result = await trader.reconcile_on_startup()
        assert result == {"reconciled_closed": 0, "still_open": 0, "errors": 0}
    finally:
        trader.state.close()


@pytest.mark.asyncio
async def test_reconciler_closes_flat_broker_position(tmp_path):
    """Broker 측 flat 인데 state 는 'open' → closed_manual 로 mark."""
    trader = _make_trader(tmp_path)
    try:
        trader.state.open_position(
            symbol="BTCUSDT", side="long",
            entry_ts_iso="2026-05-27T01:00:00+00:00",
            entry_px=100.0, qty=2.0,
            stop_px=97.0, tp_px=106.0,
            fire_key="key1",
        )
        # DummyBroker 의 get_open_position_qty 는 0.0 반환 (flat)
        result = await trader.reconcile_on_startup()
        assert result["reconciled_closed"] == 1
        assert result["still_open"] == 0
        assert trader.state.count_open() == 0
    finally:
        trader.state.close()


@pytest.mark.asyncio
async def test_reconciler_keeps_broker_open_position(tmp_path):
    """Broker 에 포지션 있으면 state 유지."""
    trader = _make_trader(tmp_path)
    try:
        trader.state.open_position(
            symbol="BTCUSDT", side="long",
            entry_ts_iso="2026-05-27T01:00:00+00:00",
            entry_px=100.0, qty=2.0,
            stop_px=97.0, tp_px=106.0,
            fire_key="key1",
        )
        # broker 가 LONG 2.0 보유 중이라고 응답
        trader.broker.get_open_position_qty = AsyncMock(return_value=2.0)
        result = await trader.reconcile_on_startup()
        assert result["still_open"] == 1
        assert result["reconciled_closed"] == 0
        assert trader.state.count_open() == 1
    finally:
        trader.state.close()


@pytest.mark.asyncio
async def test_reconciler_error_counted(tmp_path):
    """Broker API 에러 시 errors 증가 + 포지션 유지."""
    trader = _make_trader(tmp_path)
    try:
        trader.state.open_position(
            symbol="BTCUSDT", side="long",
            entry_ts_iso="2026-05-27T01:00:00+00:00",
            entry_px=100.0, qty=2.0,
            stop_px=97.0, tp_px=106.0,
            fire_key="key1",
        )
        trader.broker.get_open_position_qty = AsyncMock(
            side_effect=RuntimeError("network"),
        )
        result = await trader.reconcile_on_startup()
        assert result["errors"] == 1
        assert result["reconciled_closed"] == 0
        # 에러 시 포지션 유지 (계속 보유)
        assert trader.state.count_open() == 1
    finally:
        trader.state.close()


@pytest.mark.asyncio
async def test_reconciler_mixed_positions(tmp_path):
    """3개 포지션 중 1개 flat, 1개 open, 1개 error."""
    trader = _make_trader(tmp_path)
    try:
        for i, sym in enumerate(["BTCUSDT", "ETHUSDT", "SOLUSDT"]):
            trader.state.open_position(
                symbol=sym, side="long",
                entry_ts_iso=f"2026-05-27T0{i}:00:00+00:00",
                entry_px=100.0, qty=1.0,
                stop_px=97.0, tp_px=106.0,
                fire_key=f"k{i}",
            )
        # symbol 별 다른 응답
        async def mock_qty(symbol: str) -> float:
            if symbol == "BTCUSDT":
                return 0.0  # flat
            if symbol == "ETHUSDT":
                return 5.0  # open
            raise ConnectionError("eth network")
        # SOLUSDT raises but actually we want different per call
        async def mock_qty_v2(symbol: str) -> float:
            if symbol == "BTCUSDT":
                return 0.0
            if symbol == "ETHUSDT":
                return 5.0
            if symbol == "SOLUSDT":
                raise ConnectionError("sol network")
            return 0.0
        trader.broker.get_open_position_qty = mock_qty_v2
        result = await trader.reconcile_on_startup()
        assert result == {
            "reconciled_closed": 1,
            "still_open": 1,
            "errors": 1,
        }
        # BTC 만 closed
        assert trader.state.count_open() == 2
    finally:
        trader.state.close()
