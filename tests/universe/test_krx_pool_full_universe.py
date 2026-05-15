"""S7-part2 (#231) — get_full_universe_codes returns KOSPI200 entire constituent set."""
from __future__ import annotations

import pytest

from src.universe.krx_pool import (
    KOSPI200_CONSTITUENTS,
    get_full_universe_codes,
    get_pool_codes,
)


def test_full_universe_returns_all_kospi200_constituents():
    """Full universe size matches KOSPI200_CONSTITUENTS count (~197)."""
    codes = get_full_universe_codes()
    assert len(codes) == len(KOSPI200_CONSTITUENTS)
    assert len(codes) >= 190, "KOSPI200 should yield >=190 constituents"


def test_full_universe_deterministic_order():
    """Same call twice → identical list (sorted by code)."""
    a = get_full_universe_codes()
    b = get_full_universe_codes()
    assert a == b
    assert a == sorted(a), "must be sorted by code for stable cron output"


def test_full_universe_all_codes_unique():
    codes = get_full_universe_codes()
    assert len(set(codes)) == len(codes)


def test_full_universe_includes_samsung():
    """005930 (Samsung Electronics) is canonical KOSPI200 member."""
    assert "005930" in get_full_universe_codes()


def test_full_universe_vs_legacy_pool_30_overlap():
    """Legacy 30-sample is a subset of the full universe."""
    full = set(get_full_universe_codes())
    pool30 = set(get_pool_codes(n=30, seed=42))
    assert pool30.issubset(full)


def test_full_universe_sector_filter():
    """sector filter narrows the set without exception."""
    constituents = KOSPI200_CONSTITUENTS
    sectors_available = sorted({c["sector"] for c in constituents})
    if not sectors_available:
        pytest.skip("no sectors in test data")
    one_sector = [sectors_available[0]]
    filtered = get_full_universe_codes(sectors=one_sector)
    assert len(filtered) < len(KOSPI200_CONSTITUENTS)
    assert all(isinstance(c, str) for c in filtered)
