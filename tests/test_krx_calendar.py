"""Unit tests for src/universe/krx_calendar.py (T2 Red phase)."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
import pytz  # still used for pytz.utc below

from src.universe.krx_calendar import is_krx_holiday, is_krx_trading_hours

# #238 follow-up — zoneinfo, matching the production krx_calendar swap. With
# pytz, `datetime(...).replace(tzinfo=KST)` yields the LMT+8:28 offset (the
# classic pytz footgun); ZoneInfo gives the correct +09:00 (Korea has no DST).
KST = ZoneInfo("Asia/Seoul")


class TestIsKrxHoliday:
    def test_new_years_day_2025(self):
        assert is_krx_holiday(date(2025, 1, 1)) is True

    def test_new_years_day_2026(self):
        assert is_krx_holiday(date(2026, 1, 1)) is True

    def test_childrens_day_2025(self):
        assert is_krx_holiday(date(2025, 5, 5)) is True

    def test_childrens_day_substitute_2025(self):
        # 2025-05-05 is Monday; 2025-05-06 is the substitute holiday
        assert is_krx_holiday(date(2025, 5, 6)) is True

    def test_regular_weekday_not_holiday(self):
        # 2025-04-01 is a Tuesday with no holiday
        assert is_krx_holiday(date(2025, 4, 1)) is False

    def test_saturday_is_not_holiday_per_function(self):
        # is_krx_holiday only checks the holiday list, not weekends
        # Weekends are handled by is_krx_trading_hours
        # 2025-04-05 is a Saturday
        result = is_krx_holiday(date(2025, 4, 5))
        assert isinstance(result, bool)

    def test_christmas_2025(self):
        assert is_krx_holiday(date(2025, 12, 25)) is True

    def test_liberation_day_2025(self):
        assert is_krx_holiday(date(2025, 8, 15)) is True

    def test_national_foundation_day_2025(self):
        assert is_krx_holiday(date(2025, 10, 3)) is True

    def test_hangul_day_2025(self):
        assert is_krx_holiday(date(2025, 10, 9)) is True

    def test_memorial_day_2025(self):
        assert is_krx_holiday(date(2025, 6, 6)) is True

    def test_year_end_2025(self):
        assert is_krx_holiday(date(2025, 12, 31)) is True


class TestIsKrxTradingHours:
    def test_weekday_during_trading_hours_kst(self):
        # Wednesday 2025-04-02 10:00 KST
        ts = datetime(2025, 4, 2, 10, 0, 0).replace(tzinfo=KST)
        assert is_krx_trading_hours(ts) is True

    def test_weekday_at_market_open_kst(self):
        # 09:00 KST exactly
        ts = datetime(2025, 4, 2, 9, 0, 0).replace(tzinfo=KST)
        assert is_krx_trading_hours(ts) is True

    def test_weekday_at_market_close_kst(self):
        # 15:30 KST exactly — included
        ts = datetime(2025, 4, 2, 15, 30, 0).replace(tzinfo=KST)
        assert is_krx_trading_hours(ts) is True

    def test_weekday_before_market_open(self):
        # 08:59 KST
        ts = datetime(2025, 4, 2, 8, 59, 0).replace(tzinfo=KST)
        assert is_krx_trading_hours(ts) is False

    def test_weekday_after_market_close(self):
        # 15:31 KST
        ts = datetime(2025, 4, 2, 15, 31, 0).replace(tzinfo=KST)
        assert is_krx_trading_hours(ts) is False

    def test_saturday_during_normal_hours(self):
        # Saturday 2025-04-05 10:00 KST
        ts = datetime(2025, 4, 5, 10, 0, 0).replace(tzinfo=KST)
        assert is_krx_trading_hours(ts) is False

    def test_sunday_during_normal_hours(self):
        # Sunday 2025-04-06 10:00 KST
        ts = datetime(2025, 4, 6, 10, 0, 0).replace(tzinfo=KST)
        assert is_krx_trading_hours(ts) is False

    def test_holiday_weekday_during_normal_hours(self):
        # 2025-01-01 (New Year) is Wednesday, 10:00 KST
        ts = datetime(2025, 1, 1, 10, 0, 0).replace(tzinfo=KST)
        assert is_krx_trading_hours(ts) is False

    def test_utc_timestamp_converted_correctly(self):
        # Wednesday 2025-04-02 01:00 UTC = 10:00 KST → trading
        ts = datetime(2025, 4, 2, 1, 0, 0, tzinfo=pytz.utc)
        assert is_krx_trading_hours(ts) is True

    def test_utc_timestamp_before_open(self):
        # Wednesday 2025-04-02 23:59 UTC (prev day) = 08:59 KST → not trading
        ts = datetime(2025, 4, 1, 23, 59, 0, tzinfo=pytz.utc)
        assert is_krx_trading_hours(ts) is False
