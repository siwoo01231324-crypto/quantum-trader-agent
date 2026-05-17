"""src/live/schedule.py — wait_until_session_open async 헬퍼 단위 테스트 (#216 US-002)."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from src.live.schedule import wait_until_session_open
from src.universe.krx_calendar import KST


def kst(y: int, m: int, d: int, h: int = 0, mn: int = 0) -> datetime:
    return datetime(y, m, d, h, mn).replace(tzinfo=KST)


class TestWaitUntilSessionOpen:
    """schedule 게이트 동작 — sleep 호출 여부 + 결과 시각 검증."""

    @pytest.mark.asyncio
    async def test_always_returns_now_no_sleep(self):
        sleep_mock = AsyncMock()
        now = kst(2026, 5, 9, 23, 59)  # Saturday late
        result = await wait_until_session_open(
            "always", now_fn=lambda: now, sleep_fn=sleep_mock,
        )
        assert sleep_mock.call_count == 0
        assert result == now

    @pytest.mark.asyncio
    async def test_krx_during_session_returns_now_no_sleep(self):
        sleep_mock = AsyncMock()
        now = kst(2026, 5, 8, 12, 0)  # Fri 12:00 KST (장중)
        result = await wait_until_session_open(
            "krx", now_fn=lambda: now, sleep_fn=sleep_mock,
        )
        assert sleep_mock.call_count == 0
        assert result == now

    @pytest.mark.asyncio
    async def test_krx_saturday_sleeps_until_monday_open(self):
        sleep_mock = AsyncMock()
        now = kst(2026, 5, 9, 10, 0)  # Sat 10:00
        result = await wait_until_session_open(
            "krx", now_fn=lambda: now, sleep_fn=sleep_mock,
        )
        assert sleep_mock.call_count == 1
        delay_called = sleep_mock.call_args[0][0]
        # Mon 5/11 09:00 - Sat 5/9 10:00 = 47 hours
        assert abs(delay_called - 47 * 3600) < 60
        assert result == kst(2026, 5, 11, 9, 0)

    @pytest.mark.asyncio
    async def test_krx_weekday_after_close_sleeps_until_next_day(self):
        sleep_mock = AsyncMock()
        now = kst(2026, 5, 8, 16, 0)  # Fri 16:00 (마감 후)
        result = await wait_until_session_open(
            "krx", now_fn=lambda: now, sleep_fn=sleep_mock,
        )
        assert sleep_mock.call_count == 1
        delay_called = sleep_mock.call_args[0][0]
        # Mon 5/11 09:00 - Fri 5/8 16:00 = 65 hours
        assert abs(delay_called - 65 * 3600) < 60
        assert result == kst(2026, 5, 11, 9, 0)

    @pytest.mark.asyncio
    async def test_krx_holiday_morning_sleeps_to_next_business_day(self):
        sleep_mock = AsyncMock()
        now = kst(2026, 5, 5, 8, 0)  # Tue 어린이날 08:00
        result = await wait_until_session_open(
            "krx", now_fn=lambda: now, sleep_fn=sleep_mock,
        )
        assert sleep_mock.call_count == 1
        # Next biz: Wed 5/6 09:00
        assert result == kst(2026, 5, 6, 9, 0)

    @pytest.mark.asyncio
    async def test_krx_pre_open_returns_today_open_no_sleep(self):
        # 평일 08:00 (장 시작 1시간 전) → 같은 날 09:00, sleep 1시간
        sleep_mock = AsyncMock()
        now = kst(2026, 5, 8, 8, 0)
        result = await wait_until_session_open(
            "krx", now_fn=lambda: now, sleep_fn=sleep_mock,
        )
        assert sleep_mock.call_count == 1
        delay_called = sleep_mock.call_args[0][0]
        assert abs(delay_called - 3600) < 60
        assert result == kst(2026, 5, 8, 9, 0)

    @pytest.mark.asyncio
    async def test_unknown_schedule_raises(self):
        with pytest.raises(ValueError, match="schedule"):
            await wait_until_session_open("never")
