"""Tests for ``data_lake.fetcher.fetch_binance_futures_klines`` (#106).

Validates the Binance Futures USDT-M klines endpoint
(`https://fapi.binance.com/fapi/v1/klines`) integration:
- OHLCV_SCHEMA conformance with ``source="binance_futures"`` (distinct from Spot)
- 1000-row pagination
- 429 exponential backoff
- Multi-symbol fan-out (BTCUSDT/ETHUSDT/SOLUSDT)

Uses ``responses`` library to mock HTTP — no live network calls.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
import responses as responses_lib

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from data_lake import OHLCV_SCHEMA
from data_lake.fetcher import (
    BINANCE_FUTURES_KLINES_URL,
    BINANCE_KLINES_URL,
    fetch_binance_futures_klines,
    save_ohlcv_parquet,
)


def _make_kline(
    open_time: int = 1_700_000_000_000,
    open_: str = "30000.0",
    high: str = "31000.0",
    low: str = "29000.0",
    close: str = "30500.0",
    volume: str = "100.0",
    close_time: int = 1_700_000_899_999,
    quote_vol: str = "3050000.0",
    trades: int = 500,
    taker_buy_base: str = "50.0",
    taker_buy_quote: str = "1525000.0",
    ignore: str = "0",
) -> list:
    """Single Binance kline row (12-element list, identical wire format on Spot/Futures)."""
    return [
        open_time, open_, high, low, close, volume,
        close_time, quote_vol, trades, taker_buy_base, taker_buy_quote, ignore,
    ]


# ---------------------------------------------------------------------------
# URL constant + endpoint distinction
# ---------------------------------------------------------------------------

def test_futures_klines_url_targets_fapi():
    """Futures URL must point to fapi.binance.com (not api.binance.com)."""
    assert BINANCE_FUTURES_KLINES_URL == "https://fapi.binance.com/fapi/v1/klines"
    assert BINANCE_FUTURES_KLINES_URL != BINANCE_KLINES_URL


# ---------------------------------------------------------------------------
# OHLCV schema + source label
# ---------------------------------------------------------------------------

@responses_lib.activate
def test_futures_response_parsed_with_source_binance_futures():
    """Single-page Futures response → OHLCV_SCHEMA + source='binance_futures'."""
    kline = _make_kline()
    responses_lib.add(
        responses_lib.GET, BINANCE_FUTURES_KLINES_URL, json=[kline], status=200,
    )

    df = fetch_binance_futures_klines(
        symbol="BTCUSDT", interval="1m",
        start="2023-11-14", end="2023-11-15",
    )

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1

    expected_cols = set(OHLCV_SCHEMA.keys())
    assert set(df.columns) == expected_cols

    row = df.iloc[0]
    assert row["symbol"] == "BTCUSDT"
    assert row["freq"] == "1m"
    assert row["source"] == "binance_futures"  # distinct from Spot's "binance"
    assert float(row["open"]) == pytest.approx(30000.0)
    assert float(row["close"]) == pytest.approx(30500.0)
    assert float(row["volume"]) == pytest.approx(100.0)
    assert float(row["vwap"]) == pytest.approx(3050000.0 / 100.0)
    assert int(row["trade_count"]) == 500
    assert isinstance(row["ts"], pd.Timestamp)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

@responses_lib.activate
def test_futures_paginates_with_limit_1000(monkeypatch):
    """1000-row page → fetcher requests next page; total 1002 rows in 2 calls."""
    monkeypatch.setattr("data_lake.fetcher.time.sleep", lambda _: None)
    base_time = 1_700_000_000_000
    interval_ms = 60 * 1000  # 1m

    page1 = [_make_kline(open_time=base_time + i * interval_ms) for i in range(1000)]
    last_open_p1 = base_time + 999 * interval_ms
    page2 = [
        _make_kline(open_time=last_open_p1 + interval_ms),
        _make_kline(open_time=last_open_p1 + 2 * interval_ms),
    ]

    responses_lib.add(responses_lib.GET, BINANCE_FUTURES_KLINES_URL, json=page1, status=200)
    responses_lib.add(responses_lib.GET, BINANCE_FUTURES_KLINES_URL, json=page2, status=200)

    df = fetch_binance_futures_klines(
        symbol="BTCUSDT", interval="1m",
        start="2023-11-14", end="2024-12-31",
    )

    assert len(df) == 1002
    assert len(responses_lib.calls) == 2
    assert "startTime" in responses_lib.calls[1].request.url


# ---------------------------------------------------------------------------
# 429 backoff retry
# ---------------------------------------------------------------------------

@responses_lib.activate
def test_futures_rate_limit_retry_on_429(monkeypatch):
    """429 → exponential backoff → 200."""
    sleep_calls: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

    kline = _make_kline()
    responses_lib.add(
        responses_lib.GET, BINANCE_FUTURES_KLINES_URL,
        json={"msg": "Too Many Requests"}, status=429,
    )
    responses_lib.add(
        responses_lib.GET, BINANCE_FUTURES_KLINES_URL, json=[kline], status=200,
    )

    df = fetch_binance_futures_klines(
        symbol="BTCUSDT", interval="1m",
        start="2023-11-14", end="2023-11-15",
    )

    assert len(df) == 1
    assert len(responses_lib.calls) == 2
    assert any(s >= 1.0 for s in sleep_calls)


# ---------------------------------------------------------------------------
# Empty response
# ---------------------------------------------------------------------------

@responses_lib.activate
def test_futures_empty_response_returns_schema_columns():
    """Empty JSON list → empty DataFrame with full OHLCV_SCHEMA columns."""
    responses_lib.add(
        responses_lib.GET, BINANCE_FUTURES_KLINES_URL, json=[], status=200,
    )

    df = fetch_binance_futures_klines(
        symbol="DOESNOTEXISTUSDT", interval="1m",
        start="2023-11-14", end="2023-11-15",
    )

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0
    assert set(df.columns) == set(OHLCV_SCHEMA.keys())


# ---------------------------------------------------------------------------
# Multi-symbol — AC requires BTCUSDT/ETHUSDT/SOLUSDT
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
@responses_lib.activate
def test_futures_supports_required_symbols(symbol):
    """AC: BTCUSDT/ETHUSDT/SOLUSDT 1m bar download must work via this fetcher."""
    kline = _make_kline()
    responses_lib.add(
        responses_lib.GET, BINANCE_FUTURES_KLINES_URL, json=[kline], status=200,
    )

    df = fetch_binance_futures_klines(
        symbol=symbol, interval="1m",
        start="2023-11-14", end="2023-11-15",
    )

    assert len(df) == 1
    assert df.iloc[0]["symbol"] == symbol
    assert df.iloc[0]["source"] == "binance_futures"
    assert df.iloc[0]["freq"] == "1m"


# ---------------------------------------------------------------------------
# Parquet roundtrip — confirm Futures lake reads back via load_ohlcv_from_parquet
# ---------------------------------------------------------------------------

@responses_lib.activate
def test_futures_parquet_roundtrip(tmp_path):
    """fetch → save_ohlcv_parquet → backtest.bundle.load_ohlcv_from_parquet roundtrip."""
    from backtest.bundle import load_ohlcv_from_parquet

    open_time_ms = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
    kline = _make_kline(open_time=open_time_ms)
    responses_lib.add(
        responses_lib.GET, BINANCE_FUTURES_KLINES_URL, json=[kline], status=200,
    )

    df = fetch_binance_futures_klines(
        symbol="BTCUSDT", interval="1m",
        start="2024-06-01", end="2024-06-02",
    )
    saved = save_ohlcv_parquet(df, tmp_path, symbol="BTCUSDT", freq="1m")
    assert len(saved) == 1

    read_df = load_ohlcv_from_parquet(tmp_path, symbol="BTCUSDT", freq="1m")
    assert len(read_df) == 1
    assert read_df.iloc[0]["source"] == "binance_futures"
