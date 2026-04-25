"""KRX trading calendar helpers.

Static 2025-2026 holiday list for KRX (Korea Exchange).
Separate from src/execution/krx_handler.py (single-auction order buffer).
"""
from __future__ import annotations

from datetime import date, datetime, time

import pytz

KST = pytz.timezone("Asia/Seoul")

# KRX official holidays 2025-2026 (static, pin-date 2026-04-25)
# Sources: KRX annual market holiday announcements
_KRX_HOLIDAYS: frozenset[date] = frozenset([
    # 2025
    date(2025, 1, 1),   # 신정
    date(2025, 1, 28),  # 설날 연휴
    date(2025, 1, 29),  # 설날
    date(2025, 1, 30),  # 설날 연휴
    date(2025, 3, 1),   # 삼일절
    date(2025, 5, 5),   # 어린이날
    date(2025, 5, 6),   # 어린이날 대체공휴일 (2025-05-05가 월요일 → 화요일 대체)
    date(2025, 5, 15),  # 부처님오신날 (석가탄신일)
    date(2025, 6, 6),   # 현충일
    date(2025, 8, 15),  # 광복절
    date(2025, 10, 3),  # 개천절
    date(2025, 10, 6),  # 추석 연휴
    date(2025, 10, 7),  # 추석
    date(2025, 10, 8),  # 추석 연휴
    date(2025, 10, 9),  # 한글날
    date(2025, 12, 25), # 크리스마스
    date(2025, 12, 31), # 연말 휴장

    # 2026
    date(2026, 1, 1),   # 신정
    date(2026, 1, 26),  # 설날 연휴
    date(2026, 1, 27),  # 설날
    date(2026, 1, 28),  # 설날 연휴
    date(2026, 3, 1),   # 삼일절 (일요일 → 3/2 대체 가능, 보수적으로 3/1 포함)
    date(2026, 3, 2),   # 삼일절 대체공휴일
    date(2026, 5, 5),   # 어린이날
    date(2026, 5, 25),  # 부처님오신날
    date(2026, 6, 6),   # 현충일 (토요일 → 월요일 대체 없음, 당일만)
    date(2026, 8, 15),  # 광복절 (토요일 → 월요일 대체 없음)
    date(2026, 9, 24),  # 추석 연휴
    date(2026, 9, 25),  # 추석
    date(2026, 9, 26),  # 추석 연휴
    date(2026, 10, 3),  # 개천절 (토요일 → 대체 없음)
    date(2026, 10, 9),  # 한글날
    date(2026, 12, 25), # 크리스마스
    date(2026, 12, 31), # 연말 휴장
])

_MARKET_OPEN = time(9, 0, 0)
_MARKET_CLOSE = time(15, 30, 0)


def is_krx_holiday(d: date) -> bool:
    """Return True if the given date is a KRX market holiday.

    Does NOT check for weekends — use is_krx_trading_hours for full trading-day logic.
    """
    return d in _KRX_HOLIDAYS


def is_krx_trading_hours(ts: datetime) -> bool:
    """Return True if ts falls within KRX regular trading hours.

    Conditions: weekday (Mon-Fri), not a KRX holiday, 09:00-15:30 KST inclusive.

    Args:
        ts: timezone-aware datetime (any timezone; converted to KST internally).
    """
    if ts.tzinfo is None:
        raise ValueError("ts must be timezone-aware")

    ts_kst = ts.astimezone(KST)
    if ts_kst.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    if is_krx_holiday(ts_kst.date()):
        return False
    t = ts_kst.time().replace(tzinfo=None)
    return _MARKET_OPEN <= t <= _MARKET_CLOSE
