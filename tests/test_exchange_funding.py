"""Tests for exchange_funding fetchers (OKX and Bybit).

Uses unittest.mock to avoid real network calls.
Covers: normal pagination, empty response, rate-limit retry, multi-page.
"""
from __future__ import annotations

from datetime import timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data_lake.exchange_funding.okx import fetch_funding_history as okx_fetch
from src.data_lake.exchange_funding.bybit import fetch_funding_history as bybit_fetch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts_ms(iso: str) -> int:
    return int(pd.Timestamp(iso, tz="UTC").timestamp() * 1000)


def _okx_response(rows: list[dict], code: str = "0") -> dict:
    return {"code": code, "data": rows}


def _bybit_response(rows: list[dict], ret_code: int = 0) -> dict:
    return {"retCode": ret_code, "result": {"list": rows}}


def _okx_row(ts_ms: int, rate: float) -> dict:
    return {"fundingTime": str(ts_ms), "fundingRate": str(rate)}


def _bybit_row(ts_ms: int, rate: float) -> dict:
    return {"fundingRateTimestamp": str(ts_ms), "fundingRate": str(rate)}


# ---------------------------------------------------------------------------
# OKX tests
# ---------------------------------------------------------------------------

class TestOkxFetch:
    def test_single_page(self):
        ts1 = _ts_ms("2024-01-01T00:00:00")
        ts2 = _ts_ms("2024-01-01T08:00:00")
        rows = [_okx_row(ts2, 0.0001), _okx_row(ts1, -0.0002)]  # OKX returns newest-first

        with patch("src.data_lake.exchange_funding.okx.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = _okx_response(rows)
            mock_get.return_value = resp

            df = okx_fetch("BTC-USDT-SWAP", "2024-01-01", "2024-01-02")

        assert len(df) == 2
        assert list(df.columns) == ["ts", "funding_rate"]
        # sorted ascending
        assert df["ts"].iloc[0] < df["ts"].iloc[1]
        assert df["funding_rate"].iloc[0] == pytest.approx(-0.0002)
        assert df["funding_rate"].iloc[1] == pytest.approx(0.0001)
        # ts is UTC-aware
        assert df["ts"].iloc[0].tzinfo is not None

    def test_empty_response_returns_empty_df(self):
        with patch("src.data_lake.exchange_funding.okx.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = _okx_response([])
            mock_get.return_value = resp

            df = okx_fetch("BTC-USDT-SWAP", "2024-01-01", "2024-01-02")

        assert df.empty
        assert list(df.columns) == ["ts", "funding_rate"]

    def test_api_error_returns_empty_df(self):
        with patch("src.data_lake.exchange_funding.okx.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"code": "51001", "msg": "Instrument ID does not exist", "data": []}
            mock_get.return_value = resp

            df = okx_fetch("INVALID-SWAP", "2024-01-01", "2024-01-02")

        assert df.empty

    def test_rate_limit_retry(self):
        ts1 = _ts_ms("2024-01-01T00:00:00")
        rows = [_okx_row(ts1, 0.0001)]

        with patch("src.data_lake.exchange_funding.okx.requests.get") as mock_get, \
             patch("src.data_lake.exchange_funding.okx.time.sleep"):
            rate_limited = MagicMock()
            rate_limited.status_code = 429
            rate_limited.raise_for_status.return_value = None

            success = MagicMock()
            success.status_code = 200
            success.json.return_value = _okx_response(rows)

            mock_get.side_effect = [rate_limited, success]

            df = okx_fetch("BTC-USDT-SWAP", "2024-01-01", "2024-01-02")

        assert len(df) == 1

    def test_multi_page_pagination(self):
        # Two pages: first page full (100 rows simulated as 2 for speed), second page partial
        ts_base = _ts_ms("2024-01-01T00:00:00")
        interval_ms = 8 * 3600 * 1000  # 8h in ms

        # Page 1: 2 rows (newest first), cursor set to earliest
        page1_rows = [
            _okx_row(ts_base + interval_ms, 0.0002),
            _okx_row(ts_base, 0.0001),
        ]
        # Page 2: 1 row (older), stops pagination
        page2_rows = [
            _okx_row(ts_base - interval_ms, -0.0001),
        ]

        call_count = 0

        def side_effect(url, params, timeout):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 200
            if call_count == 1:
                # Return full page (simulate limit=100 by patching _LIMIT to 2)
                resp.json.return_value = _okx_response(page1_rows)
            else:
                resp.json.return_value = _okx_response(page2_rows)
            return resp

        with patch("src.data_lake.exchange_funding.okx.requests.get", side_effect=side_effect), \
             patch("src.data_lake.exchange_funding.okx._LIMIT", 2), \
             patch("src.data_lake.exchange_funding.okx.time.sleep"):
            df = okx_fetch("BTC-USDT-SWAP", "2020-01-01", "2025-01-01")

        assert len(df) == 3
        assert df["ts"].is_monotonic_increasing

    def test_filters_records_before_start(self):
        start_ms = _ts_ms("2024-06-01")
        before_start_ms = _ts_ms("2024-05-31T16:00:00")
        in_range_ms = _ts_ms("2024-06-01T00:00:00")

        rows = [
            _okx_row(in_range_ms, 0.0001),
            _okx_row(before_start_ms, 0.0002),  # before start, should be filtered
        ]

        with patch("src.data_lake.exchange_funding.okx.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = _okx_response(rows)
            mock_get.return_value = resp

            df = okx_fetch("BTC-USDT-SWAP", "2024-06-01", "2025-01-01")

        assert len(df) == 1
        assert df["ts"].iloc[0] >= pd.Timestamp("2024-06-01", tz="UTC")


# ---------------------------------------------------------------------------
# Bybit tests
# ---------------------------------------------------------------------------

class TestBybitFetch:
    def test_single_page(self):
        ts1 = _ts_ms("2024-01-01T00:00:00")
        ts2 = _ts_ms("2024-01-01T08:00:00")
        rows = [_bybit_row(ts1, 0.0001), _bybit_row(ts2, -0.0002)]

        with patch("src.data_lake.exchange_funding.bybit.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = _bybit_response(rows)
            mock_get.return_value = resp

            df = bybit_fetch("BTCUSDT", "2024-01-01", "2024-01-02")

        assert len(df) == 2
        assert list(df.columns) == ["ts", "funding_rate"]
        assert df["ts"].is_monotonic_increasing
        assert df["ts"].iloc[0].tzinfo is not None

    def test_empty_response_returns_empty_df(self):
        with patch("src.data_lake.exchange_funding.bybit.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = _bybit_response([])
            mock_get.return_value = resp

            df = bybit_fetch("BTCUSDT", "2024-01-01", "2024-01-02")

        assert df.empty
        assert list(df.columns) == ["ts", "funding_rate"]

    def test_api_error_returns_empty_df(self):
        with patch("src.data_lake.exchange_funding.bybit.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"retCode": 10001, "retMsg": "symbol invalid", "result": {"list": []}}
            mock_get.return_value = resp

            df = bybit_fetch("INVALID", "2024-01-01", "2024-01-02")

        assert df.empty

    def test_rate_limit_retry(self):
        ts1 = _ts_ms("2024-01-01T00:00:00")
        rows = [_bybit_row(ts1, 0.0001)]

        with patch("src.data_lake.exchange_funding.bybit.requests.get") as mock_get, \
             patch("src.data_lake.exchange_funding.bybit.time.sleep"):
            rate_limited = MagicMock()
            rate_limited.status_code = 429
            rate_limited.raise_for_status.return_value = None

            success = MagicMock()
            success.status_code = 200
            success.json.return_value = _bybit_response(rows)

            mock_get.side_effect = [rate_limited, success]

            df = bybit_fetch("BTCUSDT", "2024-01-01", "2024-01-02")

        assert len(df) == 1

    def test_multi_page_pagination(self):
        ts_base = _ts_ms("2024-01-01T00:00:00")
        interval_ms = 8 * 3600 * 1000

        page1_rows = [_bybit_row(ts_base, 0.0001), _bybit_row(ts_base + interval_ms, 0.0002)]
        page2_rows = [_bybit_row(ts_base + 2 * interval_ms, 0.0003)]

        call_count = 0

        def side_effect(url, params, timeout):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 200
            if call_count == 1:
                resp.json.return_value = _bybit_response(page1_rows)
            else:
                resp.json.return_value = _bybit_response(page2_rows)
            return resp

        with patch("src.data_lake.exchange_funding.bybit.requests.get", side_effect=side_effect), \
             patch("src.data_lake.exchange_funding.bybit._LIMIT", 2), \
             patch("src.data_lake.exchange_funding.bybit.time.sleep"):
            df = bybit_fetch("BTCUSDT", "2024-01-01", "2025-01-01")

        assert len(df) == 3
        assert df["ts"].is_monotonic_increasing

    def test_funding_rate_values_correct(self):
        ts1 = _ts_ms("2024-03-15T00:00:00")
        rows = [_bybit_row(ts1, 0.000125)]

        with patch("src.data_lake.exchange_funding.bybit.requests.get") as mock_get:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = _bybit_response(rows)
            mock_get.return_value = resp

            df = bybit_fetch("BTCUSDT", "2024-03-01", "2024-04-01")

        assert df["funding_rate"].iloc[0] == pytest.approx(0.000125)
