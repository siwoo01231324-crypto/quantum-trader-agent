"""Unit tests for src.universe.krx_top — pure functions on snapshot DataFrames."""
from __future__ import annotations

import pandas as pd
import pytest

from universe.krx_top import (
    REQUIRED_COLUMNS,
    combined_top_n,
    filter_by_min_marcap,
    top_n_by_marcap,
)


def make_snapshot(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for c in REQUIRED_COLUMNS:
        if c not in df.columns:
            df[c] = None
    return df


def test_top_n_returns_top_by_marcap_descending():
    snap = make_snapshot([
        {"code": "005930", "name": "삼성전자", "market": "KOSPI", "marcap": 500e12},
        {"code": "000660", "name": "SK하이닉스", "market": "KOSPI", "marcap": 200e12},
        {"code": "035420", "name": "NAVER", "market": "KOSPI", "marcap": 30e12},
        {"code": "035720", "name": "카카오", "market": "KOSPI", "marcap": 25e12},
    ])
    assert top_n_by_marcap(snap, "KOSPI", 2) == ["005930", "000660"]
    assert top_n_by_marcap(snap, "KOSPI", 4) == ["005930", "000660", "035420", "035720"]


def test_top_n_filters_market():
    snap = make_snapshot([
        {"code": "005930", "name": "삼성전자", "market": "KOSPI", "marcap": 500e12},
        {"code": "247540", "name": "에코프로비엠", "market": "KOSDAQ", "marcap": 30e12},
    ])
    assert top_n_by_marcap(snap, "KOSPI", 5) == ["005930"]
    assert top_n_by_marcap(snap, "KOSDAQ", 5) == ["247540"]


def test_top_n_filters_non_6digit_codes():
    """ETF/ETN/우선주 일부는 6자리 숫자가 아니거나 변형 — 제외."""
    snap = make_snapshot([
        {"code": "005930", "name": "삼성전자", "market": "KOSPI", "marcap": 500e12},
        {"code": "005935", "name": "삼성전자우", "market": "KOSPI", "marcap": 60e12},
        {"code": "Q12345", "name": "잘못된코드", "market": "KOSPI", "marcap": 100e12},
        {"code": "12345", "name": "5자리코드", "market": "KOSPI", "marcap": 100e12},
    ])
    out = top_n_by_marcap(snap, "KOSPI", 10)
    assert "005930" in out
    assert "005935" in out  # 6자리 숫자라서 통과
    assert "Q12345" not in out
    assert "12345" not in out


def test_top_n_zero_or_negative_returns_empty():
    snap = make_snapshot([
        {"code": "005930", "name": "삼성전자", "market": "KOSPI", "marcap": 500e12},
    ])
    assert top_n_by_marcap(snap, "KOSPI", 0) == []
    assert top_n_by_marcap(snap, "KOSPI", -1) == []


def test_top_n_n_larger_than_universe():
    snap = make_snapshot([
        {"code": "005930", "name": "삼성전자", "market": "KOSPI", "marcap": 500e12},
        {"code": "000660", "name": "SK하이닉스", "market": "KOSPI", "marcap": 200e12},
    ])
    assert top_n_by_marcap(snap, "KOSPI", 10) == ["005930", "000660"]


def test_top_n_validates_required_columns():
    df = pd.DataFrame({"code": ["005930"], "name": ["삼성"]})
    with pytest.raises(ValueError, match="missing required columns"):
        top_n_by_marcap(df, "KOSPI", 1)


def test_combined_top_n_concats_kospi_and_kosdaq():
    snap = make_snapshot([
        {"code": "005930", "name": "삼성전자", "market": "KOSPI", "marcap": 500e12},
        {"code": "000660", "name": "SK하이닉스", "market": "KOSPI", "marcap": 200e12},
        {"code": "247540", "name": "에코프로비엠", "market": "KOSDAQ", "marcap": 30e12},
        {"code": "086520", "name": "에코프로", "market": "KOSDAQ", "marcap": 20e12},
    ])
    out = combined_top_n(snap, kospi_n=1, kosdaq_n=1)
    assert out == ["005930", "247540"]
    out2 = combined_top_n(snap, kospi_n=2, kosdaq_n=2)
    assert out2 == ["005930", "000660", "247540", "086520"]


def test_combined_top_n_dedupes_overlap():
    """동일 code 가 KOSPI/KOSDAQ 둘에 잘못 들어간 경우라도 1번만 등장."""
    snap = make_snapshot([
        {"code": "005930", "name": "삼성전자", "market": "KOSPI", "marcap": 500e12},
        {"code": "005930", "name": "삼성전자", "market": "KOSDAQ", "marcap": 500e12},
    ])
    out = combined_top_n(snap, kospi_n=1, kosdaq_n=1)
    assert out == ["005930"]


def test_filter_by_min_marcap():
    snap = make_snapshot([
        {"code": "005930", "name": "삼성전자", "market": "KOSPI", "marcap": 500e12},
        {"code": "000660", "name": "SK하이닉스", "market": "KOSPI", "marcap": 200e12},
        {"code": "035720", "name": "카카오", "market": "KOSPI", "marcap": 25e12},
    ])
    out = filter_by_min_marcap(snap, ["005930", "000660", "035720"], min_marcap=50e12)
    assert sorted(out) == ["000660", "005930"]
