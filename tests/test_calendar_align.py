"""Tests for src/backtest/calendar_align.py — intersect_trading_days helper."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from backtest.calendar_align import intersect_trading_days  # noqa: E402


def _make_series(start: str, periods: int, name: str = "s") -> pd.Series:
    idx = pd.date_range(start, periods=periods, freq="D")
    return pd.Series(range(periods), index=idx, dtype=float, name=name)


class TestIntersectTradingDays:
    def test_single_strategy_returns_itself(self):
        s = _make_series("2024-01-01", 10)
        df = intersect_trading_days({"a": s})
        assert list(df.columns) == ["a"]
        assert len(df) == 10

    def test_same_index_returns_full(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="D")
        s1 = pd.Series([1.0] * 5, index=idx)
        s2 = pd.Series([2.0] * 5, index=idx)
        df = intersect_trading_days({"a": s1, "b": s2})
        assert len(df) == 5
        assert set(df.columns) == {"a", "b"}

    def test_crypto_365_krx_250_intersection(self):
        """crypto 365일 + KRX 250일 → 교집합 = KRX 250일."""
        crypto_idx = pd.date_range("2024-01-01", periods=365, freq="D")
        krx_idx = pd.date_range("2024-01-01", periods=250, freq="B")  # business days only

        crypto = pd.Series(range(365), index=crypto_idx, dtype=float)
        krx = pd.Series(range(250), index=krx_idx, dtype=float)

        df = intersect_trading_days({"crypto": crypto, "krx": krx})
        common = crypto_idx.intersection(krx_idx)
        assert len(df) == len(common)
        # KRX-only dates (weekends in business-day index are already excluded)
        # Verify no date is in df that isn't in both
        for date in df.index:
            assert date in crypto_idx
            assert date in krx_idx

    def test_krx_exclusive_dates_are_dropped(self):
        """KRX 전용 날짜(다른 전략에 없는 날)는 결과에서 제외."""
        base_idx = pd.date_range("2024-01-01", periods=5, freq="D")
        extended_idx = pd.date_range("2024-01-01", periods=8, freq="D")
        s1 = pd.Series(range(5), index=base_idx, dtype=float)
        s2 = pd.Series(range(8), index=extended_idx, dtype=float)
        df = intersect_trading_days({"base": s1, "extended": s2})
        assert len(df) == 5
        assert df.index.equals(base_idx)

    def test_no_overlap_returns_empty(self):
        """완전 비겹침 인덱스 → 빈 DataFrame."""
        idx1 = pd.date_range("2023-01-01", periods=5, freq="D")
        idx2 = pd.date_range("2024-01-01", periods=5, freq="D")
        s1 = pd.Series(range(5), index=idx1, dtype=float)
        s2 = pd.Series(range(5), index=idx2, dtype=float)
        df = intersect_trading_days({"a": s1, "b": s2})
        assert len(df) == 0
        assert set(df.columns) == {"a", "b"}

    def test_empty_input_returns_empty(self):
        """빈 dict → 빈 DataFrame."""
        df = intersect_trading_days({})
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    def test_no_zero_fill(self):
        """교집합에 없는 날짜에 0 또는 NaN 이 채워지지 않음을 확인."""
        idx1 = pd.date_range("2024-01-01", periods=10, freq="D")
        idx2 = pd.date_range("2024-01-03", periods=10, freq="D")
        s1 = pd.Series(range(10), index=idx1, dtype=float)
        s2 = pd.Series(range(10), index=idx2, dtype=float)
        df = intersect_trading_days({"a": s1, "b": s2})
        # 교집합: 2024-01-03 ~ 2024-01-10 (8일)
        assert len(df) == 8
        # NaN 없음
        assert not df.isnull().any().any()

    def test_column_order_matches_input(self):
        """결과 컬럼 순서가 입력 dict 순서와 일치."""
        idx = pd.date_range("2024-01-01", periods=5, freq="D")
        df = intersect_trading_days({
            "z": pd.Series(range(5), index=idx, dtype=float),
            "a": pd.Series(range(5), index=idx, dtype=float),
            "m": pd.Series(range(5), index=idx, dtype=float),
        })
        assert list(df.columns) == ["z", "a", "m"]

    def test_three_series_intersection(self):
        """3개 전략의 3중 교집합."""
        idx_all = pd.date_range("2024-01-01", periods=10, freq="D")
        idx_ab = pd.date_range("2024-01-01", periods=7, freq="D")
        idx_c = pd.date_range("2024-01-01", periods=5, freq="D")
        s_a = pd.Series(range(7), index=idx_ab, dtype=float)
        s_b = pd.Series(range(10), index=idx_all, dtype=float)
        s_c = pd.Series(range(5), index=idx_c, dtype=float)
        df = intersect_trading_days({"a": s_a, "b": s_b, "c": s_c})
        # 교집합 = 가장 짧은 5일
        assert len(df) == 5
