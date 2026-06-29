"""채널청산 sweep 의 loop 배선 회귀 테스트 (2026-06-30).

검증:
  - SWING_CHANNEL_SWEEP off / history_lookup 미주입 → sweep_channel_exits 호출 0 (byte-identical).
  - enabled + history_lookup 주입 → 호출됨.
  - sweep_timeouts 는 양 경우 모두 정상 호출 (채널 배선이 기존 timeout sweep 무영향 박제).

`_run_timeout_sweep` 을 짧은 interval 로 잠깐 돌려 호출 카운트를 본다. intents 빈 리스트라
라우팅 루프(router/wal/execute_intents)는 진입 안 함 → 그쪽 의존성 mock 불필요.
"""
from __future__ import annotations

import asyncio

from src.live.loop import _run_timeout_sweep


class _FakeRiskManager:
    def __init__(self) -> None:
        self.timeout_calls = 0
        self.channel_calls = 0
        self.channel_lookup_seen = None

    def sweep_timeouts(self, now, price_lookup):
        self.timeout_calls += 1
        return []

    def sweep_channel_exits(self, now, history_lookup):
        self.channel_calls += 1
        self.channel_lookup_seen = history_lookup
        return []


async def _run_briefly(**kwargs) -> _FakeRiskManager:
    rm = _FakeRiskManager()
    stop = asyncio.Event()
    task = asyncio.create_task(
        _run_timeout_sweep(
            position_risk_manager=rm,
            router=None, kill_switch=None, wal=None, metrics=None,
            position_store=None, stop_event=stop,
            live_price_cache=None, interval_sec=0.01, **kwargs,
        )
    )
    await asyncio.sleep(0.06)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
    return rm


async def test_channel_sweep_off_by_default():
    """flag 미지정 → 채널 sweep 호출 0, timeout sweep 은 정상."""
    rm = await _run_briefly()
    assert rm.timeout_calls >= 1
    assert rm.channel_calls == 0


async def test_channel_sweep_enabled_calls_sweep():
    """enabled + history_lookup → 채널 sweep 호출됨, lookup 전달됨."""
    def _hist(_sym):
        return None
    rm = await _run_briefly(channel_sweep_enabled=True, history_lookup=_hist)
    assert rm.timeout_calls >= 1
    assert rm.channel_calls >= 1
    assert rm.channel_lookup_seen is _hist


async def test_channel_sweep_enabled_but_no_lookup_is_noop():
    """enabled 라도 history_lookup None 이면 no-op (안전)."""
    rm = await _run_briefly(channel_sweep_enabled=True, history_lookup=None)
    assert rm.timeout_calls >= 1
    assert rm.channel_calls == 0


async def test_timeout_sweep_unaffected_by_channel_flag():
    """채널 flag on/off 양쪽 모두 timeout sweep 동일 동작 (기존 방어 박제)."""
    off = await _run_briefly()
    on = await _run_briefly(channel_sweep_enabled=True, history_lookup=lambda s: None)
    assert off.timeout_calls >= 1 and on.timeout_calls >= 1
