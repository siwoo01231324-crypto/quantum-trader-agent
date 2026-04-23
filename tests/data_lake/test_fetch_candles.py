"""Tests for data_lake.fetcher — Binance REST → Parquet pipeline.

Uses `responses` library to mock HTTP calls without hitting the network.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest
import responses as responses_lib

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from data_lake import OHLCV_SCHEMA
from data_lake.fetcher import (
    BINANCE_KLINES_URL,
    fetch_binance_klines,
    save_ohlcv_parquet,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    """Return a single Binance kline row (list of 12 elements)."""
    return [
        open_time,
        open_,
        high,
        low,
        close,
        volume,
        close_time,
        quote_vol,
        trades,
        taker_buy_base,
        taker_buy_quote,
        ignore,
    ]


# ---------------------------------------------------------------------------
# Test 1: Binance response parsed to OHLCV schema
# ---------------------------------------------------------------------------

@responses_lib.activate
def test_binance_response_parsed_to_ohlcv_schema():
    """Mock a single-page Binance klines response and check 12-column OHLCV schema."""
    kline = _make_kline()
    responses_lib.add(
        responses_lib.GET,
        BINANCE_KLINES_URL,
        json=[kline],
        status=200,
    )

    df = fetch_binance_klines(
        symbol="BTCUSDT",
        interval="15m",
        start="2023-11-14",
        end="2023-11-15",
    )

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1

    # All 12 OHLCV_SCHEMA columns must be present
    expected_cols = set(OHLCV_SCHEMA.keys())
    actual_cols = set(df.columns)
    assert expected_cols == actual_cols, (
        f"Missing: {expected_cols - actual_cols}, Extra: {actual_cols - expected_cols}"
    )

    row = df.iloc[0]
    assert row["symbol"] == "BTCUSDT"
    assert row["freq"] == "15m"
    assert row["source"] == "binance"
    assert float(row["open"]) == pytest.approx(30000.0)
    assert float(row["high"]) == pytest.approx(31000.0)
    assert float(row["low"]) == pytest.approx(29000.0)
    assert float(row["close"]) == pytest.approx(30500.0)
    assert float(row["volume"]) == pytest.approx(100.0)
    assert float(row["vwap"]) == pytest.approx(3050000.0 / 100.0)
    assert int(row["trade_count"]) == 500
    assert isinstance(row["ts"], pd.Timestamp)
    assert isinstance(row["ingested_at"], pd.Timestamp)


# ---------------------------------------------------------------------------
# Test 2: Pagination — 1000 candles per request, advance startTime
# ---------------------------------------------------------------------------

@responses_lib.activate
def test_fetcher_paginates_with_limit_1000(monkeypatch):
    """Fetcher should paginate: send a second request when first returns 1000 rows."""
    monkeypatch.setattr("data_lake.fetcher.time.sleep", lambda _: None)
    base_time = 1_700_000_000_000
    interval_ms = 15 * 60 * 1000  # 15 minutes in ms

    # First page: 1000 candles starting at base_time
    page1 = [
        _make_kline(open_time=base_time + i * interval_ms)
        for i in range(1000)
    ]
    # Second page: 2 candles (end of data)
    last_open_time_page1 = base_time + 999 * interval_ms
    page2 = [
        _make_kline(open_time=last_open_time_page1 + interval_ms),
        _make_kline(open_time=last_open_time_page1 + 2 * interval_ms),
    ]

    responses_lib.add(responses_lib.GET, BINANCE_KLINES_URL, json=page1, status=200)
    responses_lib.add(responses_lib.GET, BINANCE_KLINES_URL, json=page2, status=200)

    df = fetch_binance_klines(
        symbol="BTCUSDT",
        interval="15m",
        start="2023-11-14",
        end="2024-12-31",
    )

    assert len(df) == 1002
    # Verify two requests were made
    assert len(responses_lib.calls) == 2
    # Second request should have startTime advanced
    second_req_params = responses_lib.calls[1].request.url
    assert "startTime" in second_req_params


# ---------------------------------------------------------------------------
# Test 3: Parquet saved to correct partition path
# ---------------------------------------------------------------------------

@responses_lib.activate
def test_parquet_saved_to_correct_partition_path(tmp_path):
    """Verify output path: ohlcv/freq=15m/year=YYYY/month=MM/symbol=BTCUSDT/part-0.parquet"""
    # ts = 2023-11-14 00:00 UTC  → year=2023, month=11
    open_time_ms = int(datetime(2023, 11, 14, tzinfo=timezone.utc).timestamp() * 1000)
    kline = _make_kline(open_time=open_time_ms)

    responses_lib.add(responses_lib.GET, BINANCE_KLINES_URL, json=[kline], status=200)

    df = fetch_binance_klines(
        symbol="BTCUSDT",
        interval="15m",
        start="2023-11-14",
        end="2023-11-15",
    )

    saved_paths = save_ohlcv_parquet(df, tmp_path, symbol="BTCUSDT", freq="15m")

    expected = tmp_path / "ohlcv" / "freq=15m" / "year=2023" / "month=11" / "symbol=BTCUSDT" / "part-0.parquet"
    assert expected in saved_paths
    assert expected.exists()


# ---------------------------------------------------------------------------
# Test 4: Parquet schema matches OHLCV_SCHEMA (12 columns)
# ---------------------------------------------------------------------------

@responses_lib.activate
def test_parquet_schema_matches_ohlcv(tmp_path):
    """Read saved parquet file and validate 12 OHLCV columns are present."""
    open_time_ms = int(datetime(2023, 11, 14, tzinfo=timezone.utc).timestamp() * 1000)
    kline = _make_kline(open_time=open_time_ms)

    responses_lib.add(responses_lib.GET, BINANCE_KLINES_URL, json=[kline], status=200)

    df = fetch_binance_klines(
        symbol="BTCUSDT",
        interval="15m",
        start="2023-11-14",
        end="2023-11-15",
    )
    saved_paths = save_ohlcv_parquet(df, tmp_path, symbol="BTCUSDT", freq="15m")

    assert len(saved_paths) > 0
    parquet_file = saved_paths[0]
    read_df = pd.read_parquet(parquet_file)

    expected_cols = set(OHLCV_SCHEMA.keys())
    actual_cols = set(read_df.columns)
    assert expected_cols == actual_cols, (
        f"Missing: {expected_cols - actual_cols}, Extra: {actual_cols - expected_cols}"
    )
    assert len(read_df) == 1


# ---------------------------------------------------------------------------
# Test 5: Rate limit retry on 429
# ---------------------------------------------------------------------------

@responses_lib.activate
def test_rate_limit_retry_on_429(monkeypatch):
    """Mock 429 → verify exponential backoff retry."""
    sleep_calls: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

    kline = _make_kline()

    # First call → 429, second call → success
    responses_lib.add(responses_lib.GET, BINANCE_KLINES_URL, json={"msg": "Too Many Requests"}, status=429)
    responses_lib.add(responses_lib.GET, BINANCE_KLINES_URL, json=[kline], status=200)

    df = fetch_binance_klines(
        symbol="BTCUSDT",
        interval="15m",
        start="2023-11-14",
        end="2023-11-15",
    )

    # Should have retried and succeeded
    assert len(df) == 1
    assert len(responses_lib.calls) == 2
    # Must have slept for exponential backoff (at least 1 second for first retry)
    assert any(s >= 1.0 for s in sleep_calls)


# ---------------------------------------------------------------------------
# Test 6: CLI args parsed correctly
# ---------------------------------------------------------------------------

def test_cli_args_parsed_correctly():
    """Validate --symbol, --interval, --start, --end, --output-dir arg parsing."""
    import importlib.util

    script_path = Path(__file__).parent.parent.parent / "scripts" / "fetch_candles.py"
    spec = importlib.util.spec_from_file_location("fetch_candles_mod", script_path)
    mod = importlib.util.module_from_spec(spec)
    # exec_module runs top-level code but main() is guarded by __name__ == "__main__"
    spec.loader.exec_module(mod)

    parser = mod.build_parser()
    args = parser.parse_args([
        "--symbol", "ETHUSDT",
        "--interval", "1h",
        "--start", "2024-01-01",
        "--end", "2024-06-01",
        "--output-dir", "/tmp/lake",
    ])

    assert args.symbol == "ETHUSDT"
    assert args.interval == "1h"
    assert args.start == "2024-01-01"
    assert args.end == "2024-06-01"
    assert args.output_dir == "/tmp/lake"
