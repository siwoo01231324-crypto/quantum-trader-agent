"""Tests for fetch_kis_intraday_ohlcv data_lake adapter."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.brokers.kis.schemas import KISIntradayBar
from src.data_lake.schema import OHLCV_SCHEMA


def _make_bar(d: str = "20260401", t: str = "090000", close: float = 100.0,
              volume: float = 1000.0, trade_amt: float = 100000.0) -> KISIntradayBar:
    return KISIntradayBar(
        date=d,
        time=t,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=volume,
        trade_amt=trade_amt,
    )


@pytest.fixture()
def mock_auth():
    return MagicMock()


@pytest.fixture()
def common_kwargs(mock_auth):
    return dict(
        auth=mock_auth,
        app_key="key",
        app_secret="secret",
        cano="12345678",
        acnt_prdt_cd="01",
        paper=True,
    )


@patch("src.data_lake.fetcher.KISClient")
@patch("src.data_lake.fetcher.fetch_intraday_ohlcv_raw")
@patch("src.data_lake.fetcher.time")
def test_schema_columns_source_freq(mock_time, mock_raw, mock_client, common_kwargs):
    """OHLCV_SCHEMA 12 컬럼 모두 존재, source=="kis", freq=="15m"."""
    mock_raw.return_value = [_make_bar("20260401", "090000")]
    today = date(2026, 4, 25)
    start = (today - timedelta(days=5)).isoformat()
    end = today.isoformat()

    with patch("src.data_lake.fetcher.datetime") as mock_dt:
        mock_dt.now.return_value = MagicMock(
            date=MagicMock(return_value=today),
            tzinfo=None,
            __sub__=MagicMock(return_value=today - timedelta(days=30)),
        )
        # Use real datetime for KST now
        import datetime as _dt
        mock_dt.now.side_effect = lambda tz=None: _dt.datetime(2026, 4, 25, 0, 0, 0,
                                                                tzinfo=_dt.timezone.utc)

        from src.data_lake.fetcher import fetch_kis_intraday_ohlcv
        df = fetch_kis_intraday_ohlcv("005930", start, end, **common_kwargs)

    assert set(df.columns) == set(OHLCV_SCHEMA.keys()), f"Missing columns: {set(OHLCV_SCHEMA.keys()) - set(df.columns)}"
    assert (df["source"] == "kis").all()
    assert (df["freq"] == "15m").all()


@patch("src.data_lake.fetcher.KISClient")
@patch("src.data_lake.fetcher.fetch_intraday_ohlcv_raw")
@patch("src.data_lake.fetcher.time")
def test_ts_utc_conversion(mock_time, mock_raw, mock_client, common_kwargs):
    """KST 09:00 → UTC 00:00, KST 15:30 → UTC 06:30."""
    mock_raw.return_value = [
        _make_bar("20260420", "090000"),
        _make_bar("20260420", "153000"),
    ]

    from src.data_lake.fetcher import fetch_kis_intraday_ohlcv
    df = fetch_kis_intraday_ohlcv("005930", "2026-04-20", "2026-04-20", **common_kwargs)

    assert len(df) == 2
    ts_list = list(df["ts"])
    # KST 09:00 = UTC 00:00
    assert ts_list[0].hour == 0 and ts_list[0].minute == 0
    # KST 15:30 = UTC 06:30
    assert ts_list[1].hour == 6 and ts_list[1].minute == 30


@patch("src.data_lake.fetcher.KISClient")
@patch("src.data_lake.fetcher.fetch_intraday_ohlcv_raw")
@patch("src.data_lake.fetcher.time")
def test_holiday_weekend_skip(mock_time, mock_raw, mock_client, common_kwargs):
    """휴일/주말은 fetch_intraday_ohlcv_raw 호출 없이 skip."""
    mock_raw.return_value = []

    from src.data_lake.fetcher import fetch_kis_intraday_ohlcv

    # 2026-04-18=Sat(skip), 2026-04-19=Sun(skip), 2026-04-20=Mon(call),
    # 2026-04-21=Tue(call), 2026-04-22=Wed(call) — none are KRX holidays
    # Patch is_krx_holiday to treat 2026-04-21 as holiday to verify skip
    with patch("src.data_lake.fetcher.is_krx_holiday", side_effect=lambda d: d.isoformat() == "2026-04-21"):
        df = fetch_kis_intraday_ohlcv("005930", "2026-04-18", "2026-04-22", **common_kwargs)

    call_count = mock_raw.call_count
    # Sat/Sun skipped by bdate_range, 2026-04-21 skipped by mocked holiday
    # Only 2026-04-20 and 2026-04-22 should be called
    assert call_count == 2
    called_dates = [c[0][2] for c in mock_raw.call_args_list]
    assert "20260420" in called_dates
    assert "20260422" in called_dates
    assert "20260421" not in called_dates


@patch("src.data_lake.fetcher.KISClient")
@patch("src.data_lake.fetcher.fetch_intraday_ohlcv_raw")
@patch("src.data_lake.fetcher.time")
def test_30day_boundary_warning(mock_time, mock_raw, mock_client, common_kwargs, caplog):
    """30일 초과 요청 → warning 로그, 가능한 일자만 반환."""
    mock_raw.return_value = [_make_bar("20260424", "090000")]

    from src.data_lake.fetcher import fetch_kis_intraday_ohlcv

    # Request: 2026-03-01 (>30 days ago from 2026-04-25) to 2026-04-24
    # 2026-03-01 and some days will be >30 days (cutoff = 2026-03-26)
    with caplog.at_level(logging.WARNING, logger="src.data_lake.fetcher"):
        df = fetch_kis_intraday_ohlcv("005930", "2026-03-20", "2026-04-24", **common_kwargs)

    # Should have warning messages for dates before cutoff (2026-03-26)
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("30d limit" in m or "skipping" in m for m in warning_msgs), \
        f"Expected 30d warning, got: {warning_msgs}"

    # Only days on/after cutoff should be fetched
    for call in mock_raw.call_args_list:
        called_date_str = call[0][2]
        called_date = date(int(called_date_str[:4]), int(called_date_str[4:6]), int(called_date_str[6:]))
        today = date(2026, 4, 25)
        cutoff = today - timedelta(days=30)
        assert called_date >= cutoff, f"{called_date} is before cutoff {cutoff}"


@patch("src.data_lake.fetcher.KISClient")
@patch("src.data_lake.fetcher.fetch_intraday_ohlcv_raw")
@patch("src.data_lake.fetcher.time")
def test_sleep_between_days(mock_time, mock_raw, mock_client, common_kwargs):
    """일자별 loop 사이 time.sleep(0.5) 호출 횟수 검증."""
    mock_raw.return_value = [_make_bar()]

    from src.data_lake.fetcher import fetch_kis_intraday_ohlcv

    # 2026-04-20 to 2026-04-24: Mon-Fri = 5 trading days (no holidays)
    fetch_kis_intraday_ohlcv("005930", "2026-04-20", "2026-04-24", **common_kwargs)

    # First call: no sleep; subsequent calls: sleep once per day
    # 5 days → 4 sleeps
    sleep_calls = [c for c in mock_time.sleep.call_args_list if c[0][0] == 0.5]
    assert len(sleep_calls) == 4, f"Expected 4 sleep(0.5) calls, got {len(sleep_calls)}"
