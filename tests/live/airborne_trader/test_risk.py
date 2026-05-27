"""Unit tests for AirborneTraderRisk — 6 gates."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from live.airborne_fire_listener import FireRecord
from live.airborne_trader.config import AirborneTraderConfig
from live.airborne_trader.risk import AirborneTraderRisk
from live.airborne_trader.state import AirborneTraderState


@pytest.fixture
def setup(tmp_path):
    state = AirborneTraderState(path=tmp_path / "state.db")
    config = AirborneTraderConfig()
    risk = AirborneTraderRisk(config, state)
    yield state, config, risk
    state.close()


def _fire(*, kst_hour: int = 11, symbol: str = "BTCUSDT",
          side: str = "long", age_seconds: float = 60.0,
          ) -> tuple[FireRecord, datetime]:
    """KST hour 정시의 fire + (fire + age) 의 now_utc."""
    from zoneinfo import ZoneInfo
    KST = ZoneInfo("Asia/Seoul")
    # 2026-05-27 KST 의 kst_hour 정시
    fire_kst = datetime(2026, 5, 27, kst_hour, 0, 30, tzinfo=KST)
    fire_ts = fire_kst.astimezone(timezone.utc)
    now_utc = fire_ts + timedelta(seconds=age_seconds)
    assert fire_ts.astimezone(KST).hour == kst_hour
    return (
        FireRecord(ts=fire_ts, symbol=symbol, side=side,
                   fire_close=100.0, trigger=99.5),
        now_utc,
    )


class TestKstHourGate:
    def test_kst_11_passes(self, setup):
        state, config, risk = setup
        fire, now = _fire(kst_hour=11)
        result = risk.evaluate(fire, now_utc=now)
        assert result.ok, result.reason

    @pytest.mark.parametrize("kst_hour", [0, 4, 7, 9, 12, 15, 17, 23])
    def test_other_hours_rejected(self, setup, kst_hour):
        state, config, risk = setup
        fire, now = _fire(kst_hour=kst_hour)
        result = risk.evaluate(fire, now_utc=now)
        assert not result.ok
        assert "kst_hour" in result.reason

    @pytest.mark.parametrize("kst_hour", [8, 11, 16, 22])
    def test_all_4_hours_pass(self, setup, kst_hour):
        state, config, risk = setup
        fire, now = _fire(kst_hour=kst_hour)
        result = risk.evaluate(fire, now_utc=now)
        assert result.ok, f"{kst_hour}: {result.reason}"


class TestStaleFireGate:
    def test_stale_rejected(self, setup):
        state, config, risk = setup
        fire, now = _fire(kst_hour=11, age_seconds=600)  # 10분 전
        result = risk.evaluate(fire, now_utc=now)
        assert not result.ok
        assert "stale_fire" in result.reason

    def test_future_fire_rejected(self, setup):
        state, config, risk = setup
        fire, now = _fire(kst_hour=11, age_seconds=-60)  # 미래
        result = risk.evaluate(fire, now_utc=now)
        assert not result.ok
        assert "future_fire" in result.reason


class TestMaxConcurrent:
    def test_blocked_when_at_max(self, setup):
        state, config, risk = setup
        # max_concurrent (10) 만큼 채우기
        for i in range(config.max_concurrent_positions):
            state.open_position(
                symbol=f"SYM{i}USDT", side="long",
                entry_ts_iso="2026-01-01T00:00:00+00:00",
                entry_px=100, qty=1, stop_px=97, tp_px=106,
                fire_key=f"k{i}",
            )
        fire, now = _fire(kst_hour=11, symbol="NEWUSDT")
        result = risk.evaluate(fire, now_utc=now)
        assert not result.ok
        assert "max_concurrent" in result.reason


class TestSameSymbolBlock:
    def test_blocked_when_already_open(self, setup):
        state, config, risk = setup
        state.open_position(
            symbol="BTCUSDT", side="long",
            entry_ts_iso="2026-05-27T00:00:00+00:00",
            entry_px=100, qty=1, stop_px=97, tp_px=106,
            fire_key="existing",
        )
        fire, now = _fire(kst_hour=11, symbol="BTCUSDT")
        result = risk.evaluate(fire, now_utc=now)
        assert not result.ok
        assert "already_open" in result.reason

    def test_different_symbol_ok(self, setup):
        state, config, risk = setup
        state.open_position(
            symbol="BTCUSDT", side="long",
            entry_ts_iso="2026-05-27T00:00:00+00:00",
            entry_px=100, qty=1, stop_px=97, tp_px=106,
            fire_key="existing",
        )
        fire, now = _fire(kst_hour=11, symbol="ETHUSDT")
        result = risk.evaluate(fire, now_utc=now)
        assert result.ok, result.reason


class TestCooldown:
    def test_blocked_within_cooldown(self, setup):
        state, config, risk = setup
        # fire 는 KST 11:00:30 정시, now 는 +30s 뒤
        fire, now_utc = _fire(kst_hour=11, symbol="BTCUSDT")
        # 5분 전에 BTCUSDT stop_loss
        five_min_ago = now_utc - timedelta(seconds=300)
        pid = state.open_position(
            symbol="BTCUSDT", side="long",
            entry_ts_iso=(five_min_ago - timedelta(seconds=600)).isoformat(),
            entry_px=100, qty=1, stop_px=97, tp_px=106,
            fire_key="prev",
        )
        state.close_position(
            position_id=pid, exit_ts_iso=five_min_ago.isoformat(),
            exit_px=97, status="closed_sl", realized_pnl_usd=-3.0,
        )
        result = risk.evaluate(fire, now_utc=now_utc)
        assert not result.ok
        assert "cooldown" in result.reason

    def test_passes_after_cooldown(self, setup):
        state, config, risk = setup
        fire, now_utc = _fire(kst_hour=11, symbol="BTCUSDT")
        # 20 분 전에 stop_loss (cooldown 900s = 15분)
        twenty_min_ago = now_utc - timedelta(seconds=1200)
        pid = state.open_position(
            symbol="BTCUSDT", side="long",
            entry_ts_iso=(twenty_min_ago - timedelta(seconds=600)).isoformat(),
            entry_px=100, qty=1, stop_px=97, tp_px=106,
            fire_key="prev",
        )
        state.close_position(
            position_id=pid, exit_ts_iso=twenty_min_ago.isoformat(),
            exit_px=97, status="closed_sl", realized_pnl_usd=-3.0,
        )
        result = risk.evaluate(fire, now_utc=now_utc)
        assert result.ok, result.reason


class TestDailyLossLimit:
    def test_blocked_at_limit(self, setup):
        state, config, risk = setup
        fire, now_utc = _fire(kst_hour=11, symbol="BTCUSDT")
        # 오늘 KST 자정 이후 -200 USDT 실현
        pid = state.open_position(
            symbol="X", side="long",
            entry_ts_iso="2026-05-26T16:00:00+00:00",
            entry_px=100, qty=1, stop_px=97, tp_px=106,
            fire_key="loss",
        )
        # exit_ts 가 KST 자정 (= UTC 2026-05-26T15:00) 이후
        state.close_position(
            position_id=pid, exit_ts_iso="2026-05-26T16:00:00+00:00",
            exit_px=97, status="closed_sl", realized_pnl_usd=-200.0,
        )
        result = risk.evaluate(fire, now_utc=now_utc)
        assert not result.ok
        assert "daily_loss_limit" in result.reason
