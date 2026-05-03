"""Unit tests for src.backtest.calendar_align (#132 coverage 0% → 95%+)."""
from __future__ import annotations

import pandas as pd

from src.backtest.calendar_align import intersect_trading_days


def _idx(*dates: str) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(d) for d in dates])


# ---------------------------------------------------------------------------
# Empty / degenerate cases
# ---------------------------------------------------------------------------

def test_empty_dict_returns_empty_df():
    out = intersect_trading_days({})
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_single_series_passthrough():
    s = pd.Series([0.01, 0.02, 0.03], index=_idx("2026-01-01", "2026-01-02", "2026-01-03"))
    out = intersect_trading_days({"only": s})
    assert list(out.columns) == ["only"]
    assert len(out) == 3
    assert (out["only"].values == s.values).all()


# ---------------------------------------------------------------------------
# Two-series intersection
# ---------------------------------------------------------------------------

def test_identical_indexes_full_intersection():
    idx = _idx("2026-01-01", "2026-01-02")
    a = pd.Series([0.01, 0.02], index=idx)
    b = pd.Series([0.03, 0.04], index=idx)
    out = intersect_trading_days({"a": a, "b": b})
    assert len(out) == 2
    assert list(out.columns) == ["a", "b"]


def test_partial_overlap_keeps_only_common_dates():
    a = pd.Series([0.01, 0.02, 0.03], index=_idx("2026-01-01", "2026-01-02", "2026-01-03"))
    b = pd.Series([0.04, 0.05], index=_idx("2026-01-02", "2026-01-04"))
    out = intersect_trading_days({"a": a, "b": b})
    # 교집합은 2026-01-02 만
    assert len(out) == 1
    assert out.index[0] == pd.Timestamp("2026-01-02")
    assert out.iloc[0]["a"] == 0.02
    assert out.iloc[0]["b"] == 0.04


def test_disjoint_indexes_returns_empty_with_columns():
    a = pd.Series([0.01], index=_idx("2026-01-01"))
    b = pd.Series([0.02], index=_idx("2026-02-01"))
    out = intersect_trading_days({"a": a, "b": b})
    assert out.empty
    assert list(out.columns) == ["a", "b"]


# ---------------------------------------------------------------------------
# Three+ series
# ---------------------------------------------------------------------------

def test_three_series_intersection():
    """공통 거래일은 모든 series 에 존재하는 날짜만."""
    a = pd.Series([0.01, 0.02, 0.03], index=_idx("2026-01-01", "2026-01-02", "2026-01-03"))
    b = pd.Series([0.04, 0.05], index=_idx("2026-01-02", "2026-01-03"))
    c = pd.Series([0.06], index=_idx("2026-01-03"))
    out = intersect_trading_days({"a": a, "b": b, "c": c})
    assert len(out) == 1
    assert out.index[0] == pd.Timestamp("2026-01-03")


def test_columns_order_matches_input_dict_order():
    a = pd.Series([0.01], index=_idx("2026-01-01"))
    b = pd.Series([0.02], index=_idx("2026-01-01"))
    c = pd.Series([0.03], index=_idx("2026-01-01"))
    out = intersect_trading_days({"strat_z": a, "strat_a": b, "strat_m": c})
    # dict 순서 그대로 보존 (Python 3.7+)
    assert list(out.columns) == ["strat_z", "strat_a", "strat_m"]


# ---------------------------------------------------------------------------
# Realistic KRX/Crypto cross-asset alignment
# ---------------------------------------------------------------------------

def test_krx_excludes_weekends_crypto_includes_them():
    """KRX 평일만, crypto 7일 — 주말은 교집합에서 제외."""
    krx = pd.Series(
        [0.01, 0.02, 0.03],
        index=_idx("2026-01-05", "2026-01-06", "2026-01-07"),  # Mon, Tue, Wed
    )
    crypto = pd.Series(
        [0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.007],
        index=_idx(
            "2026-01-03", "2026-01-04",  # Sat, Sun
            "2026-01-05", "2026-01-06", "2026-01-07",  # Mon, Tue, Wed
            "2026-01-08", "2026-01-09",  # Thu, Fri
        ),
    )
    out = intersect_trading_days({"krx": krx, "crypto": crypto})
    # 교집합은 평일 3일
    assert len(out) == 3
    # 주말은 결과에 없음
    assert pd.Timestamp("2026-01-03") not in out.index
    assert pd.Timestamp("2026-01-04") not in out.index


def test_no_nan_fill():
    """차이나는 날짜는 dropped — NaN 으로 채우지 않음."""
    a = pd.Series([0.01, 0.02], index=_idx("2026-01-01", "2026-01-02"))
    b = pd.Series([0.03], index=_idx("2026-01-01"))
    out = intersect_trading_days({"a": a, "b": b})
    assert not out.isna().any().any()
    assert len(out) == 1
