"""Unit tests for fetch_kis_daily_ohlcv in src/data_lake/fetcher.py (T1 Red phase).

All network calls are mocked.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.brokers.kis.schemas import KISDailyBar
from src.data_lake.fetcher import fetch_kis_daily_ohlcv
from data_lake import OHLCV_SCHEMA


def _make_auth() -> MagicMock:
    auth = MagicMock()
    auth.get_token.return_value = "fake_token"
    return auth


def _make_bars(n: int = 3) -> list[KISDailyBar]:
    bars = []
    for i in range(n):
        date = f"2026010{i+1}"
        bars.append(KISDailyBar(
            date=date,
            open=70000.0 + i * 100,
            high=71000.0 + i * 100,
            low=69000.0 + i * 100,
            close=70500.0 + i * 100,
            volume=1000000.0 + i * 10000,
            trade_amt=70000000000.0,
        ))
    return bars


COMMON_KWARGS = dict(
    auth=_make_auth(),
    app_key="FAKE_KEY",
    app_secret="FAKE_SECRET",
    cano="12345678",
    acnt_prdt_cd="01",
    paper=True,
)


# ---------------------------------------------------------------------------
# 1. Schema validation
# ---------------------------------------------------------------------------

class TestOhlcvSchema:
    def test_returns_dataframe_with_all_schema_columns(self):
        bars = _make_bars(3)
        with patch("src.data_lake.fetcher.fetch_daily_ohlcv_raw", return_value=bars):
            df = fetch_kis_daily_ohlcv("005930", "2026-01-01", "2026-01-03", **COMMON_KWARGS)

        assert isinstance(df, pd.DataFrame)
        for col in OHLCV_SCHEMA:
            assert col in df.columns, f"Missing column: {col}"

    def test_source_column_is_kis(self):
        bars = _make_bars(2)
        with patch("src.data_lake.fetcher.fetch_daily_ohlcv_raw", return_value=bars):
            df = fetch_kis_daily_ohlcv("005930", "2026-01-01", "2026-01-02", **COMMON_KWARGS)

        assert (df["source"] == "kis").all()

    def test_symbol_column_matches_input(self):
        bars = _make_bars(2)
        with patch("src.data_lake.fetcher.fetch_daily_ohlcv_raw", return_value=bars):
            df = fetch_kis_daily_ohlcv("005930", "2026-01-01", "2026-01-02", **COMMON_KWARGS)

        assert (df["symbol"] == "005930").all()

    def test_freq_column_is_1d(self):
        bars = _make_bars(2)
        with patch("src.data_lake.fetcher.fetch_daily_ohlcv_raw", return_value=bars):
            df = fetch_kis_daily_ohlcv("005930", "2026-01-01", "2026-01-02", **COMMON_KWARGS)

        assert (df["freq"] == "1d").all()

    def test_ts_column_is_utc_datetime(self):
        bars = _make_bars(2)
        with patch("src.data_lake.fetcher.fetch_daily_ohlcv_raw", return_value=bars):
            df = fetch_kis_daily_ohlcv("005930", "2026-01-01", "2026-01-02", **COMMON_KWARGS)

        assert pd.api.types.is_datetime64_any_dtype(df["ts"])

    def test_ohlcv_numeric_columns_are_float(self):
        bars = _make_bars(2)
        with patch("src.data_lake.fetcher.fetch_daily_ohlcv_raw", return_value=bars):
            df = fetch_kis_daily_ohlcv("005930", "2026-01-01", "2026-01-02", **COMMON_KWARGS)

        for col in ["open", "high", "low", "close", "volume"]:
            assert pd.api.types.is_float_dtype(df[col]), f"{col} not float"


# ---------------------------------------------------------------------------
# 2. Empty response
# ---------------------------------------------------------------------------

class TestEmptyResponse:
    def test_empty_bars_returns_empty_dataframe(self):
        with patch("src.data_lake.fetcher.fetch_daily_ohlcv_raw", return_value=[]):
            df = fetch_kis_daily_ohlcv("005930", "2026-01-01", "2026-01-02", **COMMON_KWARGS)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0
        for col in OHLCV_SCHEMA:
            assert col in df.columns


# ---------------------------------------------------------------------------
# 3. Pagination mock — multiple bars
# ---------------------------------------------------------------------------

class TestPaginationMock:
    def test_multiple_bars_all_returned(self):
        bars = _make_bars(10)
        with patch("src.data_lake.fetcher.fetch_daily_ohlcv_raw", return_value=bars):
            df = fetch_kis_daily_ohlcv("005930", "2026-01-01", "2026-01-10", **COMMON_KWARGS)

        assert len(df) == 10

    def test_row_values_match_input_bars(self):
        bars = _make_bars(1)
        with patch("src.data_lake.fetcher.fetch_daily_ohlcv_raw", return_value=bars):
            df = fetch_kis_daily_ohlcv("005930", "2026-01-01", "2026-01-01", **COMMON_KWARGS)

        row = df.iloc[0]
        assert row["open"] == pytest.approx(70000.0)
        assert row["high"] == pytest.approx(71000.0)
        assert row["low"] == pytest.approx(69000.0)
        assert row["close"] == pytest.approx(70500.0)
        assert row["volume"] == pytest.approx(1000000.0)


# ---------------------------------------------------------------------------
# 4. KISClient construction — credentials passed through
# ---------------------------------------------------------------------------

class TestClientConstruction:
    def test_fetch_daily_ohlcv_raw_called_with_correct_symbol(self):
        bars = _make_bars(1)
        with patch("src.data_lake.fetcher.fetch_daily_ohlcv_raw", return_value=bars) as mock_raw:
            with patch("src.data_lake.fetcher.KISClient") as mock_client_cls:
                fetch_kis_daily_ohlcv("000660", "2026-01-01", "2026-01-05", **COMMON_KWARGS)

        mock_raw.assert_called_once()
        call_args = mock_raw.call_args
        assert call_args[0][1] == "000660"  # symbol positional arg

    def test_paper_flag_passed_to_client(self):
        bars = _make_bars(1)
        with patch("src.data_lake.fetcher.fetch_daily_ohlcv_raw", return_value=bars):
            with patch("src.data_lake.fetcher.KISClient") as mock_client_cls:
                mock_client_cls.return_value = MagicMock()
                fetch_kis_daily_ohlcv(
                    "005930", "2026-01-01", "2026-01-01",
                    auth=_make_auth(), app_key="K", app_secret="S",
                    cano="12345678", acnt_prdt_cd="01", paper=False,
                )

        _, kwargs = mock_client_cls.call_args
        assert kwargs.get("paper") is False
