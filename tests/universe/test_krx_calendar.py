"""KRX trading calendar 헬퍼 단위 테스트 (#216)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.universe.krx_calendar import (
    KST,
    is_krx_holiday,
    is_krx_trading_hours,
    next_session_open,
)


def kst(y: int, m: int, d: int, h: int = 0, mn: int = 0) -> datetime:
    return datetime(y, m, d, h, mn).replace(tzinfo=KST)


class TestNextSessionOpen:
    """next_session_open: 다음 KRX 영업일 09:00 KST 계산.

    AC 매핑 (PRD US-001):
      - 평일 09:00 이전 → 같은 날 09:00
      - 평일 장중 (09:00~15:30) → 같은 날 09:00 (이미 시작됨 신호)
      - 평일 15:30 이후 → 다음 영업일 09:00
      - 토요일/일요일 → 다음 월요일 09:00 (월요일이 holiday 면 그 다음 영업일)
      - holiday → 그 다음 영업일 09:00
    """

    def test_weekday_morning_before_open(self):
        # 2026-05-08 (Fri, 영업일) 08:00 → 같은 날 09:00
        now = kst(2026, 5, 8, 8, 0)
        assert next_session_open(now) == kst(2026, 5, 8, 9, 0)

    def test_weekday_during_session(self):
        # 2026-05-08 12:00 → 같은 날 09:00 (이미 시작됨 표시)
        now = kst(2026, 5, 8, 12, 0)
        assert next_session_open(now) == kst(2026, 5, 8, 9, 0)

    def test_weekday_after_close(self):
        # 2026-05-08 (Fri) 16:00 → 다음 영업일 = Mon 5/11 09:00
        now = kst(2026, 5, 8, 16, 0)
        assert next_session_open(now) == kst(2026, 5, 11, 9, 0)

    def test_saturday(self):
        # 2026-05-09 (Sat) 10:00 → Mon 5/11 09:00
        now = kst(2026, 5, 9, 10, 0)
        assert next_session_open(now) == kst(2026, 5, 11, 9, 0)

    def test_sunday(self):
        # 2026-05-10 (Sun) 23:59 → Mon 5/11 09:00 (5/11 영업일)
        now = kst(2026, 5, 10, 23, 59)
        assert next_session_open(now) == kst(2026, 5, 11, 9, 0)

    def test_holiday_skip_to_next_business_day(self):
        # 2026-05-05 (Tue, 어린이날) 10:00 → next biz = Wed 5/6 09:00
        now = kst(2026, 5, 5, 10, 0)
        assert next_session_open(now) == kst(2026, 5, 6, 9, 0)

    def test_consecutive_holidays_lunar_new_year(self):
        # 설날 연휴: Mon 2026-01-26 + Tue 2026-01-27 + Wed 2026-01-28
        # 1/26 Mon 12:00 → next biz = Thu 2026-01-29 09:00
        now = kst(2026, 1, 26, 12, 0)
        assert next_session_open(now) == kst(2026, 1, 29, 9, 0)

    def test_friday_before_holiday_monday(self):
        # 2026-03-02 (Mon) 가 삼일절 대체공휴일 → 2026-02-27 (Fri) 16:00 → 3/3 (Tue) 09:00
        now = kst(2026, 2, 27, 16, 0)
        assert next_session_open(now) == kst(2026, 3, 3, 9, 0)

    def test_naive_datetime_raises(self):
        with pytest.raises(ValueError):
            next_session_open(datetime(2026, 5, 8, 12, 0))

    def test_utc_input_converted_to_kst(self):
        # 2026-05-08 03:00 UTC = 2026-05-08 12:00 KST (장중) → today 09:00
        now = datetime(2026, 5, 8, 3, 0, tzinfo=timezone.utc)
        assert next_session_open(now) == kst(2026, 5, 8, 9, 0)

    def test_returns_timezone_aware_kst(self):
        result = next_session_open(kst(2026, 5, 8, 8, 0))
        assert result.tzinfo is not None
        # KST utcoffset = +9:00
        assert result.utcoffset().total_seconds() == 9 * 3600


class TestExistingHelpersStillWork:
    """is_krx_holiday + is_krx_trading_hours 회귀 — 기존 동작 유지."""

    def test_is_krx_holiday_known_dates(self):
        from datetime import date
        assert is_krx_holiday(date(2026, 1, 1))   # 신정
        assert is_krx_holiday(date(2026, 5, 5))   # 어린이날
        assert not is_krx_holiday(date(2026, 5, 8))  # 평일

    def test_is_krx_trading_hours_during_session(self):
        assert is_krx_trading_hours(kst(2026, 5, 8, 10, 0))

    def test_is_krx_trading_hours_outside_session(self):
        assert not is_krx_trading_hours(kst(2026, 5, 8, 16, 0))
        assert not is_krx_trading_hours(kst(2026, 5, 9, 10, 0))  # Saturday
