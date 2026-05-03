"""Tests for src/universe/krx_pool.py."""
from __future__ import annotations

from src.universe.krx_pool import get_pool, get_pool_codes


def test_length():
    codes = get_pool_codes(30)
    assert len(codes) == 30


def test_unique():
    codes = get_pool_codes(30)
    assert len(set(codes)) == 30


def test_samsung_included():
    assert "005930" in get_pool_codes(30)


def test_determinism():
    a = get_pool_codes(30, seed=42)
    b = get_pool_codes(30, seed=42)
    assert a == b


def test_different_seeds_differ():
    a = get_pool_codes(30, seed=42)
    b = get_pool_codes(30, seed=99)
    assert a != b


def test_sector_filter():
    codes = get_pool_codes(10, sectors=["반도체"])
    from src.universe.kospi200 import KOSPI200_CONSTITUENTS
    semi_codes = {c["code"] for c in KOSPI200_CONSTITUENTS if c["sector"] == "반도체"}
    for code in codes:
        assert code in semi_codes


def test_get_pool_returns_dicts():
    pool = get_pool(10)
    assert len(pool) == 10
    for entry in pool:
        assert "code" in entry
        assert "name" in entry
        assert "sector" in entry


def test_get_pool_matches_codes():
    codes = get_pool_codes(15, seed=42)
    pool = get_pool(15, seed=42)
    assert [e["code"] for e in pool] == codes


def test_small_n():
    codes = get_pool_codes(5)
    assert len(codes) == 5
    assert len(set(codes)) == 5
