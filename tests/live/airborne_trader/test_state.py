"""Unit tests for AirborneTraderState (SQLite WAL)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from live.airborne_trader.state import (
    AirborneTraderState,
    FireDecision,
)


@pytest.fixture
def state(tmp_path):
    s = AirborneTraderState(path=tmp_path / "state.db")
    yield s
    s.close()


class TestFireDedup:
    def test_initial_unseen(self, state):
        assert not state.is_fire_processed("any:key")

    def test_record_and_check(self, state):
        state.record_fire_decision(
            fire_key="2026-05-27T07:00:33+00:00:BTCUSDT:long",
            ts_iso="2026-05-27T07:00:33+00:00",
            symbol="BTCUSDT", side="long",
            decision=FireDecision.PLACED, reason="ok",
        )
        assert state.is_fire_processed("2026-05-27T07:00:33+00:00:BTCUSDT:long")

    def test_record_idempotent(self, state):
        for _ in range(3):
            state.record_fire_decision(
                fire_key="X", ts_iso="2026-01-01T00:00:00+00:00",
                symbol="BTC", side="long",
                decision=FireDecision.SKIPPED, reason="r",
            )
        assert state.is_fire_processed("X")


class TestPositions:
    def test_open_and_list(self, state):
        pid = state.open_position(
            symbol="BTCUSDT", side="long",
            entry_ts_iso="2026-05-27T07:00:00+00:00",
            entry_px=100.0, qty=2.0,
            stop_px=97.0, tp_px=106.0,
            fire_key="key1",
        )
        assert pid > 0
        open_positions = state.list_open_positions()
        assert len(open_positions) == 1
        assert open_positions[0].symbol == "BTCUSDT"
        assert state.count_open() == 1

    def test_close_position(self, state):
        pid = state.open_position(
            symbol="BTCUSDT", side="long",
            entry_ts_iso="2026-05-27T07:00:00+00:00",
            entry_px=100.0, qty=2.0,
            stop_px=97.0, tp_px=106.0,
            fire_key="key1",
        )
        state.close_position(
            position_id=pid,
            exit_ts_iso="2026-05-27T08:00:00+00:00",
            exit_px=106.0, status="closed_tp",
            realized_pnl_usd=12.0,
        )
        assert state.count_open() == 0
        assert state.list_open_positions() == []

    def test_invalid_close_status(self, state):
        pid = state.open_position(
            symbol="X", side="long",
            entry_ts_iso="2026-01-01T00:00:00+00:00",
            entry_px=1, qty=1, stop_px=0.97, tp_px=1.06, fire_key="x",
        )
        with pytest.raises(ValueError, match="unknown close status"):
            state.close_position(
                position_id=pid, exit_ts_iso="t", exit_px=1,
                status="bogus", realized_pnl_usd=0,
            )

    def test_find_open_by_symbol(self, state):
        state.open_position(
            symbol="BTCUSDT", side="long",
            entry_ts_iso="2026-05-27T07:00:00+00:00",
            entry_px=100.0, qty=2.0,
            stop_px=97.0, tp_px=106.0,
            fire_key="key-btc",
        )
        found = state.find_open_by_symbol("BTCUSDT")
        assert found is not None
        assert found.symbol == "BTCUSDT"
        assert state.find_open_by_symbol("ETHUSDT") is None

    def test_unique_fire_key(self, state):
        state.open_position(
            symbol="BTCUSDT", side="long",
            entry_ts_iso="2026-05-27T07:00:00+00:00",
            entry_px=100.0, qty=2.0,
            stop_px=97.0, tp_px=106.0,
            fire_key="dup",
        )
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            state.open_position(
                symbol="ETHUSDT", side="short",
                entry_ts_iso="t", entry_px=1, qty=1,
                stop_px=1.03, tp_px=0.94, fire_key="dup",
            )


class TestRealizedPnl:
    def test_pnl_since_midnight(self, state):
        # 자정 이후 +12 USDT TP
        pid1 = state.open_position(
            symbol="X", side="long", entry_ts_iso="2026-05-27T01:00:00+00:00",
            entry_px=100, qty=1, stop_px=97, tp_px=106, fire_key="k1",
        )
        state.close_position(
            position_id=pid1, exit_ts_iso="2026-05-27T02:00:00+00:00",
            exit_px=106, status="closed_tp", realized_pnl_usd=6.0,
        )
        # 자정 이전 (전날) — 포함 안 되어야
        pid2 = state.open_position(
            symbol="Y", side="long", entry_ts_iso="2026-05-26T22:00:00+00:00",
            entry_px=100, qty=1, stop_px=97, tp_px=106, fire_key="k2",
        )
        state.close_position(
            position_id=pid2, exit_ts_iso="2026-05-26T23:00:00+00:00",
            exit_px=106, status="closed_tp", realized_pnl_usd=99.0,
        )
        midnight = "2026-05-27T00:00:00+00:00"
        assert state.realized_pnl_since(midnight) == pytest.approx(6.0)


class TestStopHistory:
    def test_last_stop_close_ts(self, state):
        # 2건 stop_loss
        pid = state.open_position(
            symbol="X", side="long",
            entry_ts_iso="2026-05-27T01:00:00+00:00",
            entry_px=100, qty=1, stop_px=97, tp_px=106, fire_key="k1",
        )
        state.close_position(
            position_id=pid, exit_ts_iso="2026-05-27T02:00:00+00:00",
            exit_px=97, status="closed_sl", realized_pnl_usd=-3.0,
        )
        pid2 = state.open_position(
            symbol="X", side="short",
            entry_ts_iso="2026-05-27T05:00:00+00:00",
            entry_px=100, qty=1, stop_px=103, tp_px=94, fire_key="k2",
        )
        state.close_position(
            position_id=pid2, exit_ts_iso="2026-05-27T06:00:00+00:00",
            exit_px=103, status="closed_sl", realized_pnl_usd=-3.0,
        )
        # most recent
        assert state.last_stop_close_ts("X") == "2026-05-27T06:00:00+00:00"
        # 다른 symbol — None
        assert state.last_stop_close_ts("Y") is None

    def test_tp_close_excluded_from_stop_history(self, state):
        pid = state.open_position(
            symbol="X", side="long", entry_ts_iso="t",
            entry_px=100, qty=1, stop_px=97, tp_px=106, fire_key="k",
        )
        state.close_position(
            position_id=pid, exit_ts_iso="2026-05-27T02:00:00+00:00",
            exit_px=106, status="closed_tp", realized_pnl_usd=6.0,
        )
        assert state.last_stop_close_ts("X") is None


class TestPersistence:
    def test_close_and_reopen(self, tmp_path):
        path = tmp_path / "state.db"
        s1 = AirborneTraderState(path=path)
        s1.open_position(
            symbol="BTCUSDT", side="long",
            entry_ts_iso="2026-05-27T07:00:00+00:00",
            entry_px=100.0, qty=2.0,
            stop_px=97.0, tp_px=106.0,
            fire_key="key1",
        )
        s1.close()
        # Reopen
        s2 = AirborneTraderState(path=path)
        try:
            assert s2.count_open() == 1
        finally:
            s2.close()
