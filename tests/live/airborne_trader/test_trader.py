"""Unit tests for AirborneTrader — handle_fire + monitor_positions cycles."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from live.airborne_fire_listener import AirborneFireListener, FireRecord
from live.airborne_trader.config import AirborneTraderConfig
from live.airborne_trader.risk import AirborneTraderRisk
from live.airborne_trader.state import AirborneTraderState
from live.airborne_trader.trader import AirborneTrader, DummyBroker


@pytest.fixture
def trader(tmp_path):
    state = AirborneTraderState(path=tmp_path / "state.db")
    config = AirborneTraderConfig(
        # dry_run 기본값 True 그대로
        position_usd=200.0,
        max_concurrent_positions=10,
    )
    risk = AirborneTraderRisk(config, state)
    listener = AirborneFireListener()
    broker = DummyBroker()
    # KST 11:01:00 = UTC 02:01:00. fire (KST 11:00:30) 보다 30s 미래.
    fixed_now = datetime(2026, 5, 27, 2, 1, tzinfo=timezone.utc)

    t = AirborneTrader(
        config=config, state=state, risk=risk,
        listener=listener, broker=broker,
        now_provider=lambda: fixed_now,
    )
    yield t
    state.close()


def _fire_at_kst11(symbol: str = "BTCUSDT", side: str = "long") -> FireRecord:
    """KST 11:00:30 (UTC 02:00:30) 정시 fire — 게이트 {8,11,16,22} 통과."""
    from zoneinfo import ZoneInfo
    KST = ZoneInfo("Asia/Seoul")
    fire_kst = datetime(2026, 5, 27, 11, 0, 30, tzinfo=KST)
    return FireRecord(
        ts=fire_kst.astimezone(timezone.utc),
        symbol=symbol, side=side, fire_close=100.0, trigger=99.5,
    )


class TestHandleFireDryRun:
    @pytest.mark.asyncio
    async def test_places_position_in_dry_run(self, trader):
        fire = _fire_at_kst11()
        await trader.handle_fire(fire)
        assert trader.state.count_open() == 1
        pos = trader.state.list_open_positions()[0]
        assert pos.symbol == "BTCUSDT"
        assert pos.side == "long"
        assert pos.entry_px == pytest.approx(100.0)
        # qty = 200 USDT / 100 px = 2.0
        assert pos.qty == pytest.approx(2.0)
        # stop = 100 × 0.97 / tp = 100 × 1.06
        assert pos.stop_px == pytest.approx(97.0)
        assert pos.tp_px == pytest.approx(106.0)

    @pytest.mark.asyncio
    async def test_records_fire_decision(self, trader):
        fire = _fire_at_kst11()
        await trader.handle_fire(fire)
        key = ":".join(fire.key())
        assert trader.state.is_fire_processed(key)

    @pytest.mark.asyncio
    async def test_skips_duplicate_fire(self, trader):
        fire = _fire_at_kst11()
        await trader.handle_fire(fire)
        await trader.handle_fire(fire)  # 두 번째 — 이미 처리됨
        assert trader.state.count_open() == 1

    @pytest.mark.asyncio
    async def test_skips_kst_hour_gate(self, trader):
        # KST 04 (UTC 19:00 전날) — 게이트 차단
        now = datetime(2026, 5, 27, 19, 0, tzinfo=timezone.utc)
        fire = FireRecord(
            ts=now - timedelta(seconds=60),
            symbol="BTCUSDT", side="long",
            fire_close=100, trigger=99.5,
        )
        trader._now = lambda: now
        await trader.handle_fire(fire)
        assert trader.state.count_open() == 0
        # fire 는 SKIPPED 로 기록
        key = ":".join(fire.key())
        assert trader.state.is_fire_processed(key)


class TestMonitorPositions:
    @pytest.mark.asyncio
    async def test_closes_on_tp(self, trader):
        # 진입
        fire = _fire_at_kst11()
        await trader.handle_fire(fire)
        pos = trader.state.list_open_positions()[0]
        # mark = TP 도달
        trader.broker.mark_prices[pos.symbol] = pos.tp_px
        await trader.monitor_positions()
        assert trader.state.count_open() == 0
        # 실현손익 = (106 - 100) × 2 = 12
        # 다음 check: 실현 PnL since today midnight
        closed_pnl = trader.state.realized_pnl_since(
            "2026-05-27T00:00:00+00:00"
        )
        # 2026-05-27 KST 자정 = UTC 2026-05-26T15:00
        # 우리 close ts 는 now (UTC 02:00 = KST 11) → 자정 이후
        assert closed_pnl == pytest.approx(12.0)

    @pytest.mark.asyncio
    async def test_closes_on_stop(self, trader):
        fire = _fire_at_kst11()
        await trader.handle_fire(fire)
        pos = trader.state.list_open_positions()[0]
        trader.broker.mark_prices[pos.symbol] = pos.stop_px
        await trader.monitor_positions()
        assert trader.state.count_open() == 0
        # 실현 = (97 - 100) × 2 = -6
        closed_pnl = trader.state.realized_pnl_since(
            "2026-05-27T00:00:00+00:00"
        )
        assert closed_pnl == pytest.approx(-6.0)

    @pytest.mark.asyncio
    async def test_skips_when_neither_hit(self, trader):
        fire = _fire_at_kst11()
        await trader.handle_fire(fire)
        # 가격 100 → stop/TP 둘 다 미도달
        trader.broker.mark_prices["BTCUSDT"] = 100.0
        await trader.monitor_positions()
        assert trader.state.count_open() == 1


class TestShortSide:
    @pytest.mark.asyncio
    async def test_short_stop_above_entry(self, trader):
        fire = _fire_at_kst11(side="short")
        await trader.handle_fire(fire)
        pos = trader.state.list_open_positions()[0]
        assert pos.side == "short"
        assert pos.stop_px == pytest.approx(103.0)
        assert pos.tp_px == pytest.approx(94.0)
        # mark 103 → stop
        trader.broker.mark_prices[pos.symbol] = 103.0
        await trader.monitor_positions()
        assert trader.state.count_open() == 0
        # short loss = entry - exit = 100 - 103 = -3 × qty 2 = -6
        closed_pnl = trader.state.realized_pnl_since(
            "2026-05-27T00:00:00+00:00"
        )
        assert closed_pnl == pytest.approx(-6.0)

    @pytest.mark.asyncio
    async def test_short_tp(self, trader):
        fire = _fire_at_kst11(side="short")
        await trader.handle_fire(fire)
        pos = trader.state.list_open_positions()[0]
        trader.broker.mark_prices[pos.symbol] = 94.0
        await trader.monitor_positions()
        # short profit = entry - exit = 100 - 94 = +6 × 2 = +12
        closed_pnl = trader.state.realized_pnl_since(
            "2026-05-27T00:00:00+00:00"
        )
        assert closed_pnl == pytest.approx(12.0)


class TestRunOneCycle:
    @pytest.mark.asyncio
    async def test_full_cycle(self, trader, monkeypatch):
        # listener start
        trader.listener.start_at(datetime(2026, 5, 27, 1, 0, tzinfo=timezone.utc))
        # listener 가 KST 11 fire 1건 반환
        fire = _fire_at_kst11(symbol="ETHUSDT")
        trader.listener._read_logs = lambda since_iso: (
            "2026-05-27 11:00:00,000 INFO airborne_alert_daemon — "
            "FIRE ETHUSDT long @ close=100 trigger=99.5"
        )
        trader.listener._now_utc = lambda: trader._now()
        await trader.run_one_cycle()
        assert trader.state.count_open() == 1
        assert trader.state.list_open_positions()[0].symbol == "ETHUSDT"
