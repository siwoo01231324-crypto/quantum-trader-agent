"""Unit tests for daily-loss kill switch (state + risk + trader trigger)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from live.airborne_fire_listener import AirborneFireListener, FireRecord
from live.airborne_trader.config import AirborneTraderConfig
from live.airborne_trader.risk import AirborneTraderRisk
from live.airborne_trader.state import AirborneTraderState
from live.airborne_trader.trader import AirborneTrader, DummyBroker


@pytest.fixture
def state(tmp_path):
    s = AirborneTraderState(path=tmp_path / "state.db")
    yield s
    s.close()


# ── state API ─────────────────────────────────────────────────────────────
class TestKillSwitchState:
    def test_initial_inactive(self, state):
        assert not state.is_kill_switch_active()
        assert state.last_kill_switch_event() is None

    def test_trigger_activates(self, state):
        rid = state.trigger_kill_switch("daily_loss reached")
        assert rid > 0
        assert state.is_kill_switch_active()
        last = state.last_kill_switch_event()
        assert last is not None
        assert last["reason"] == "daily_loss reached"
        assert last["unlocked_at"] is None

    def test_trigger_idempotent_while_active(self, state):
        rid1 = state.trigger_kill_switch("r1")
        rid2 = state.trigger_kill_switch("r2")
        assert rid1 == rid2  # 같은 row id — 새로 안 만들음
        # reason 은 첫 trigger 의 것
        assert state.last_kill_switch_event()["reason"] == "r1"

    def test_unlock_clears_active(self, state):
        state.trigger_kill_switch("r")
        ok = state.unlock_kill_switch(unlocked_by="test")
        assert ok is True
        assert not state.is_kill_switch_active()
        last = state.last_kill_switch_event()
        assert last["unlocked_at"] is not None
        assert last["unlocked_by"] == "test"

    def test_unlock_no_active_returns_false(self, state):
        assert state.unlock_kill_switch() is False

    def test_retrigger_after_unlock_creates_new_row(self, state):
        state.trigger_kill_switch("r1")
        state.unlock_kill_switch()
        # 두 번째 trigger 는 새 row
        rid2 = state.trigger_kill_switch("r2")
        last = state.last_kill_switch_event()
        assert last["reason"] == "r2"
        assert last["id"] == rid2
        assert state.is_kill_switch_active()


# ── risk gate ─────────────────────────────────────────────────────────────
class TestRiskGate:
    def test_blocked_when_active(self, state):
        state.trigger_kill_switch("daily_loss")
        config = AirborneTraderConfig()
        risk = AirborneTraderRisk(config, state)
        from zoneinfo import ZoneInfo
        KST = ZoneInfo("Asia/Seoul")
        fire_kst = datetime(2026, 5, 27, 11, 0, 30, tzinfo=KST)
        fire = FireRecord(
            ts=fire_kst.astimezone(timezone.utc),
            symbol="BTCUSDT", side="long",
            fire_close=100.0, trigger=99.5,
        )
        now = fire.ts + timedelta(seconds=30)
        result = risk.evaluate(fire, now_utc=now)
        assert not result.ok
        assert "kill_switch_active" in result.reason

    def test_passes_after_unlock(self, state):
        state.trigger_kill_switch("daily_loss")
        state.unlock_kill_switch()
        config = AirborneTraderConfig()
        risk = AirborneTraderRisk(config, state)
        from zoneinfo import ZoneInfo
        KST = ZoneInfo("Asia/Seoul")
        fire_kst = datetime(2026, 5, 27, 11, 0, 30, tzinfo=KST)
        fire = FireRecord(
            ts=fire_kst.astimezone(timezone.utc),
            symbol="BTCUSDT", side="long",
            fire_close=100.0, trigger=99.5,
        )
        now = fire.ts + timedelta(seconds=30)
        result = risk.evaluate(fire, now_utc=now)
        assert result.ok, result.reason


# ── trader auto-trigger ───────────────────────────────────────────────────
class TestTraderAutoTrigger:
    @pytest.mark.asyncio
    async def test_daily_loss_reject_triggers_kill_switch(self, tmp_path):
        state = AirborneTraderState(path=tmp_path / "state.db")
        try:
            config = AirborneTraderConfig()
            risk = AirborneTraderRisk(config, state)
            listener = AirborneFireListener()
            broker = DummyBroker()

            from zoneinfo import ZoneInfo
            KST = ZoneInfo("Asia/Seoul")
            fire_kst = datetime(2026, 5, 27, 11, 0, 30, tzinfo=KST)
            fire_ts = fire_kst.astimezone(timezone.utc)
            fixed_now = fire_ts + timedelta(seconds=30)

            trader = AirborneTrader(
                config=config, state=state, risk=risk,
                listener=listener, broker=broker,
                now_provider=lambda: fixed_now,
            )

            # 오늘 KST 자정 이후 -200 USDT loss 미리 기록 (limit 도달)
            pid = state.open_position(
                symbol="X", side="long",
                entry_ts_iso="2026-05-26T16:00:00+00:00",
                entry_px=100, qty=1, stop_px=97, tp_px=106, fire_key="prev",
            )
            state.close_position(
                position_id=pid, exit_ts_iso="2026-05-26T16:00:00+00:00",
                exit_px=97, status="closed_sl", realized_pnl_usd=-200.0,
            )
            assert not state.is_kill_switch_active()

            fire = FireRecord(
                ts=fire_ts, symbol="BTCUSDT", side="long",
                fire_close=100.0, trigger=99.5,
            )
            await trader.handle_fire(fire)

            # daily loss reject → kill switch 자동 trigger
            assert state.is_kill_switch_active()
            last = state.last_kill_switch_event()
            assert "daily_loss" in last["reason"]
            # 진입은 안 됨
            assert state.count_open() == 0
        finally:
            state.close()
