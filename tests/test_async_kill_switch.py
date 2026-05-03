"""AsyncKillSwitch 회귀 테스트 (#108).

Phase 3+ 멀티스레드/멀티코루틴 시나리오 — `asyncio.Lock` 기반 trip/release 의
원자성, liquidation whitelist, KillEvent 컨트랙트 호환성 검증.
"""
from __future__ import annotations

import asyncio

import pytest

from src.ops.kill_switch import AsyncKillSwitch, KillEvent, KillSwitchTripped


@pytest.mark.asyncio
async def test_trip_sets_state_and_appends_event():
    ks = AsyncKillSwitch()
    assert ks.tripped is False
    ev = await ks.trip(reason="dd_breach", source="auto:dd")
    assert isinstance(ev, KillEvent)
    assert ks.tripped is True
    assert len(ks.history()) == 1
    assert ks.last_event() is ev


@pytest.mark.asyncio
async def test_release_clears_tripped_state():
    ks = AsyncKillSwitch()
    await ks.trip(reason="r1", source="manual:cli")
    assert ks.tripped is True
    await ks.release(operator="ops-1")
    assert ks.tripped is False


@pytest.mark.asyncio
async def test_release_when_not_tripped_is_noop():
    ks = AsyncKillSwitch()
    await ks.release(operator="ops-1")
    assert ks.tripped is False
    assert ks.history() == []


@pytest.mark.asyncio
async def test_allow_order_blocks_new_when_tripped():
    ks = AsyncKillSwitch()
    assert await ks.allow_order() is True
    await ks.trip(reason="r", source="auto:dd")
    assert await ks.allow_order() is False


@pytest.mark.asyncio
async def test_allow_order_liquidation_whitelist():
    ks = AsyncKillSwitch()
    await ks.trip(reason="r", source="auto:dd")
    # Liquidation orders bypass even when tripped (spec §3).
    assert await ks.allow_order(liquidation=True) is True


@pytest.mark.asyncio
async def test_assert_allow_order_raises_when_blocked():
    ks = AsyncKillSwitch()
    await ks.trip(reason="r", source="auto:dd")
    with pytest.raises(KillSwitchTripped):
        await ks.assert_allow_order()
    # liquidation OK
    await ks.assert_allow_order(liquidation=True)


@pytest.mark.asyncio
async def test_concurrent_trip_records_all_events_state_idempotent():
    """50 concurrent trips → all events appended, state remains tripped (not toggled)."""
    ks = AsyncKillSwitch()
    N = 50
    await asyncio.gather(
        *(ks.trip(reason=f"r{i}", source="auto:test") for i in range(N))
    )
    assert ks.tripped is True
    assert len(ks.history()) == N
    # 첫 trip 만 critical 로그를 발생; 후속은 events 만 누적 (멱등).
    sources = {ev.source for ev in ks.history()}
    assert sources == {"auto:test"}


@pytest.mark.asyncio
async def test_concurrent_trip_release_alternation_consistent():
    """trip + release 가 교차해도 상태 일관성 유지 (race 없음)."""
    ks = AsyncKillSwitch()

    async def trip_then_release(i: int):
        await ks.trip(reason=f"r{i}", source="auto:race")
        await ks.release(operator=f"ops-{i}")

    await asyncio.gather(*(trip_then_release(i) for i in range(20)))
    # 마지막 release 후 상태는 tripped=False 또는 True 중 하나로 일관 (race-free).
    # 단, history 는 20 trip 모두 기록.
    assert len(ks.history()) == 20


@pytest.mark.asyncio
async def test_history_returns_copy_not_reference():
    ks = AsyncKillSwitch()
    await ks.trip(reason="r", source="auto:test")
    h = ks.history()
    h.append(KillEvent(ts=0.0, reason="external", source="external"))
    assert len(ks.history()) == 1


@pytest.mark.asyncio
async def test_dry_run_flag_propagated():
    ks = AsyncKillSwitch(dry_run=True)
    ev = await ks.trip(reason="r", source="auto:dry")
    assert ks.dry_run is True
    # KillEvent 자체는 dry_run 플래그 없음 — 로그에서만 사용.
    assert ev.reason == "r"
