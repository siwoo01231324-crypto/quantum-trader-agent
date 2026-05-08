"""Tests for KIS / Binance universe quote fetchers (#218 Phase 2 P1+P2)."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# KIS universe_quote
# ---------------------------------------------------------------------------

class _FakeBar:
    def __init__(self, dt, o, h, l, c, v):
        self.date = dt
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = v


def test_kis_bars_to_dataframe():
    from brokers.kis.universe_quote import _bars_to_dataframe
    bars = [
        _FakeBar(date(2026, 5, 1), 100, 105, 99, 104, 1000),
        _FakeBar(date(2026, 5, 2), 104, 110, 103, 109, 1200),
    ]
    df = _bars_to_dataframe(bars)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert df.iloc[0]["close"] == 104
    assert df.index[0] == pd.Timestamp("2026-05-01")


def test_kis_bars_to_dataframe_empty_returns_empty():
    from brokers.kis.universe_quote import _bars_to_dataframe
    assert _bars_to_dataframe([]).empty


def test_kis_fetch_universe_snapshot_aggregates_per_symbol(monkeypatch):
    """Mock fetch_daily_ohlcv_raw for 3 symbols → universe_snapshot returns dict.

    monkeypatch fixture 가 테스트 종료 시 sys.modules 복원 — 다른 테스트 영향 0.
    """
    import sys
    from brokers.kis import universe_quote as uq

    def fake_fetch(client, sym, start, end, period):
        return [_FakeBar(date(2026, 5, 1), 100, 105, 99, 104, 1000)]

    fake_module = MagicMock(fetch_daily_ohlcv_raw=fake_fetch)
    monkeypatch.setitem(sys.modules, "src.brokers.kis.price_client", fake_module)

    client = MagicMock()
    result = uq.fetch_universe_snapshot(
        client, ["005930", "000660", "035420"], "20260101", "20260501",
        max_workers=2, inter_call_sleep=0.0,
    )
    assert set(result.keys()) == {"005930", "000660", "035420"}
    for df in result.values():
        assert not df.empty


def test_kis_fetch_skips_failed_symbols(monkeypatch):
    """일부 심볼 fetch 실패 시 해당 심볼만 결과에서 제외."""
    import sys
    from brokers.kis import universe_quote as uq

    def fake_fetch(client, sym, start, end, period):
        if sym == "BAD":
            raise RuntimeError("simulated 5xx")
        return [_FakeBar(date(2026, 5, 1), 100, 105, 99, 104, 1000)]

    fake_module = MagicMock(fetch_daily_ohlcv_raw=fake_fetch)
    monkeypatch.setitem(sys.modules, "src.brokers.kis.price_client", fake_module)

    client = MagicMock()
    result = uq.fetch_universe_snapshot(
        client, ["GOOD1", "BAD", "GOOD2"], "20260101", "20260501",
        max_workers=1, inter_call_sleep=0.0,
    )
    assert set(result.keys()) == {"GOOD1", "GOOD2"}


# ---------------------------------------------------------------------------
# Binance universe_quote
# ---------------------------------------------------------------------------

def test_binance_klines_to_dataframe():
    from brokers.binance.universe_quote import _klines_to_dataframe
    rows = [
        [1620000000000, "100.0", "105.0", "99.0", "104.0", "1000.0",
         1620086399000, "104000.0", 50, "500.0", "52000.0", "0"],
        [1620086400000, "104.0", "110.0", "103.0", "109.0", "1200.0",
         1620172799000, "130800.0", 60, "600.0", "65400.0", "0"],
    ]
    df = _klines_to_dataframe(rows)
    assert list(df.columns) == ["open", "high", "low", "close", "volume", "quote_volume"]
    assert len(df) == 2
    assert df.iloc[0]["close"] == 104.0
    assert df.iloc[1]["quote_volume"] == 130800.0


def test_binance_klines_empty_returns_empty():
    from brokers.binance.universe_quote import _klines_to_dataframe
    assert _klines_to_dataframe([]).empty


def test_binance_fetch_universe_klines_aggregates(monkeypatch):
    from brokers.binance import universe_quote as uq

    def fake_fetch_klines(symbol, interval="1d", start_ms=None, end_ms=None,
                         limit=1000, retries=3):
        return [
            [1620000000000, "100", "105", "99", "104", "1000",
             1620086399000, "104000", 50, "500", "52000", "0"],
        ]

    monkeypatch.setattr(uq, "fetch_klines", fake_fetch_klines)
    result = uq.fetch_universe_klines(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        max_workers=2, inter_call_sleep=0.0,
    )
    assert set(result.keys()) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    for df in result.values():
        assert not df.empty
        assert "quote_volume" in df.columns


def test_binance_fetch_skips_failed_symbols(monkeypatch):
    from brokers.binance import universe_quote as uq

    def fake_fetch_klines(symbol, interval="1d", start_ms=None, end_ms=None,
                         limit=1000, retries=3):
        if symbol == "BAD":
            raise RuntimeError("simulated 5xx")
        return [
            [1620000000000, "100", "105", "99", "104", "1000",
             1620086399000, "104000", 50, "500", "52000", "0"],
        ]

    monkeypatch.setattr(uq, "fetch_klines", fake_fetch_klines)
    result = uq.fetch_universe_klines(
        ["GOOD1", "BAD", "GOOD2"],
        max_workers=1, inter_call_sleep=0.0,
    )
    assert set(result.keys()) == {"GOOD1", "GOOD2"}
