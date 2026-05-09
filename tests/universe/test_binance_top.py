"""Unit tests for src.universe.binance_top — pure functions on snapshot DataFrames."""
from __future__ import annotations

import pandas as pd
import pytest

from universe.binance_top import (
    DEFAULT_EXCLUDED_BASES,
    DEFAULT_EXCLUDED_SUFFIXES,
    REQUIRED_COLUMNS,
    top_n_by_volume,
)


def make_snapshot(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for c in REQUIRED_COLUMNS:
        if c not in df.columns:
            df[c] = None
    return df


def test_top_n_returns_top_by_volume_descending():
    snap = make_snapshot([
        {"symbol": "BTCUSDT", "last_price": 80000, "change_24h_pct": 2.0,
         "quote_volume_24h": 1.5e9},
        {"symbol": "ETHUSDT", "last_price": 2300, "change_24h_pct": 1.5,
         "quote_volume_24h": 1.0e9},
        {"symbol": "SOLUSDT", "last_price": 90, "change_24h_pct": 3.0,
         "quote_volume_24h": 0.3e9},
    ])
    assert top_n_by_volume(snap, 2) == ["BTCUSDT", "ETHUSDT"]
    assert top_n_by_volume(snap, 5) == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_excludes_stablecoin_bases():
    """USDC/FDUSD/USD1 등 stablecoin base 자동 제외."""
    snap = make_snapshot([
        {"symbol": "USDCUSDT", "last_price": 1.0, "change_24h_pct": 0.01,
         "quote_volume_24h": 2.5e9},  # 가장 높은데 제외
        {"symbol": "BTCUSDT", "last_price": 80000, "change_24h_pct": 2.0,
         "quote_volume_24h": 1.5e9},
        {"symbol": "FDUSDUSDT", "last_price": 1.0, "change_24h_pct": 0.0,
         "quote_volume_24h": 0.5e9},
    ])
    out = top_n_by_volume(snap, 5)
    assert "USDCUSDT" not in out
    assert "FDUSDUSDT" not in out
    assert out == ["BTCUSDT"]


def test_excludes_leveraged_token_suffixes():
    snap = make_snapshot([
        {"symbol": "BTCUPUSDT", "last_price": 50, "change_24h_pct": 5.0,
         "quote_volume_24h": 1e9},
        {"symbol": "BTCDOWNUSDT", "last_price": 5, "change_24h_pct": -5.0,
         "quote_volume_24h": 1e9},
        {"symbol": "ETHBULLUSDT", "last_price": 100, "change_24h_pct": 10,
         "quote_volume_24h": 0.5e9},
        {"symbol": "BTCUSDT", "last_price": 80000, "change_24h_pct": 2.0,
         "quote_volume_24h": 0.4e9},
    ])
    out = top_n_by_volume(snap, 10)
    assert out == ["BTCUSDT"]


def test_excludes_paxg_xaut_lunc_ftx():
    """PAXG/XAUT/LUNC/USTC/FTT 등 페그·소멸 자산 제외."""
    snap = make_snapshot([
        {"symbol": "PAXGUSDT", "last_price": 4700, "change_24h_pct": 0.5,
         "quote_volume_24h": 0.1e9},
        {"symbol": "LUNCUSDT", "last_price": 0.0001, "change_24h_pct": -0.3,
         "quote_volume_24h": 0.05e9},
        {"symbol": "BTCUSDT", "last_price": 80000, "change_24h_pct": 2.0,
         "quote_volume_24h": 1e9},
    ])
    out = top_n_by_volume(snap, 5)
    assert out == ["BTCUSDT"]


def test_auto_detects_stable_like_by_price_and_change():
    """가격 ~$1 + 24h 변동 0.5% 미만 → stable 자동 감지."""
    snap = make_snapshot([
        {"symbol": "MYSTABLEUSDT", "last_price": 1.001, "change_24h_pct": 0.05,
         "quote_volume_24h": 5e9},  # 자동 stable 판정
        {"symbol": "BTCUSDT", "last_price": 80000, "change_24h_pct": 2.0,
         "quote_volume_24h": 1e9},
    ])
    out = top_n_by_volume(snap, 5)
    assert out == ["BTCUSDT"]


def test_does_not_remove_low_priced_volatile_alt():
    """가격이 $1 근방이지만 24h 변동 큰 알트는 유지."""
    snap = make_snapshot([
        {"symbol": "DOGEUSDT", "last_price": 0.99, "change_24h_pct": 8.0,
         "quote_volume_24h": 1e9},
    ])
    out = top_n_by_volume(snap, 5)
    assert out == ["DOGEUSDT"]


def test_overrides_default_excludes():
    """호출자가 excluded_bases override 가능."""
    snap = make_snapshot([
        {"symbol": "USDCUSDT", "last_price": 1.0, "change_24h_pct": 0.01,
         "quote_volume_24h": 5e9},
        {"symbol": "BTCUSDT", "last_price": 80000, "change_24h_pct": 2.0,
         "quote_volume_24h": 1e9},
    ])
    # 빈 set 으로 override → 모든 base 허용 (단, stable-like 자동 감지는 살아있음)
    out = top_n_by_volume(snap, 5, excluded_bases=[])
    # USDC 는 가격 1.0 변동 0.01 → stable-like 자동 감지로 여전히 제외
    assert out == ["BTCUSDT"]


def test_only_usdt_quote_pairs():
    """BUSD/USDC quote 페어는 처리 안 함 (USDT 만)."""
    snap = make_snapshot([
        {"symbol": "BTCBUSD", "last_price": 80000, "change_24h_pct": 2.0,
         "quote_volume_24h": 5e9},
        {"symbol": "ETHUSDT", "last_price": 2300, "change_24h_pct": 1.5,
         "quote_volume_24h": 1e9},
    ])
    assert top_n_by_volume(snap, 5) == ["ETHUSDT"]


def test_zero_n_returns_empty():
    snap = make_snapshot([
        {"symbol": "BTCUSDT", "last_price": 80000, "change_24h_pct": 2.0,
         "quote_volume_24h": 1e9},
    ])
    assert top_n_by_volume(snap, 0) == []


def test_validates_required_columns():
    df = pd.DataFrame({"symbol": ["BTCUSDT"]})
    with pytest.raises(ValueError, match="missing required columns"):
        top_n_by_volume(df, 5)


def test_default_excluded_bases_set_is_immutable():
    """프로덕션 코드가 mutate 못 하도록 frozenset 보장."""
    assert isinstance(DEFAULT_EXCLUDED_BASES, frozenset)
    assert isinstance(DEFAULT_EXCLUDED_SUFFIXES, tuple)
