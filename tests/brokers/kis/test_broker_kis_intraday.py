"""Unit tests for fetch_intraday_ohlcv_raw in src/brokers/kis/price_client.py.

All HTTP calls are mocked — no real network access.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.brokers.kis.schemas import KISIntradayBar
from src.brokers.kis.price_client import fetch_intraday_ohlcv_raw


def _make_client(paper: bool = True) -> MagicMock:
    client = MagicMock()
    client._paper = paper
    if paper:
        client._base_url = "https://openapivts.koreainvestment.com:29443"
    else:
        client._base_url = "https://openapi.koreainvestment.com:9443"
    return client


def _intraday_row(date: str = "20260425", time_val: str = "093000") -> dict:
    return {
        "stck_bsop_date": date,
        "stck_cntg_hour": time_val,
        "stck_oprc": "70000",
        "stck_hgpr": "71000",
        "stck_lwpr": "69500",
        "stck_clpr": "70500",
        "acml_vol": "1234567",
        "acml_tr_pbmn": "86500000000",
    }


# ---------------------------------------------------------------------------
# 1. Single page — normal response
# ---------------------------------------------------------------------------

class TestSinglePage:
    def test_returns_list_of_intraday_bars(self):
        client = _make_client()
        # KIS returns newest-first — reverse() in fetch_intraday_ohlcv_raw makes it chronological
        rows = [
            _intraday_row("20260425", "100000"),
            _intraday_row("20260425", "094500"),
            _intraday_row("20260425", "093000"),
        ]
        client._get.return_value = {
            "rt_cd": "0",
            "output2": rows,
            "tr_cont": "",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
        }

        with patch("src.brokers.kis.price_client.time"):
            result = fetch_intraday_ohlcv_raw(client, "005930", "20260425")

        assert isinstance(result, list)
        assert len(result) == 3
        assert all(isinstance(b, KISIntradayBar) for b in result)
        # chronological order — earliest first after reverse()
        assert result[0].time == "093000"
        assert result[-1].time == "100000"


# ---------------------------------------------------------------------------
# 2. Pagination — two pages with ctx token
# ---------------------------------------------------------------------------

class TestPagination:
    def test_two_pages_concatenated_via_time_pagination(self):
        """KIS FHKST03010200 has no token-based pagination — pages are obtained
        by reducing FID_INPUT_HOUR_1 to one minute before the previous page's
        earliest bar. tr_cont is None on real responses."""
        client = _make_client()

        # Page 1: bars 15:30 down to 11:00 (5 bars, newest first)
        page1_rows = [_intraday_row("20260425", f"{h:02d}3000") for h in range(15, 10, -1)]
        # Page 2: bars 10:00 down to 09:00 (2 bars, newest first)
        page2_rows = [_intraday_row("20260425", f"{h:02d}0000") for h in range(10, 8, -1)]

        page1_response = {"rt_cd": "0", "output2": page1_rows, "tr_cont": None}
        page2_response = {"rt_cd": "0", "output2": page2_rows, "tr_cont": None}

        client._get.side_effect = [page1_response, page2_response]

        with patch("src.brokers.kis.price_client.time"):
            result = fetch_intraday_ohlcv_raw(client, "005930", "20260425", end_hhmmss="153000")

        assert len(result) == len(page1_rows) + len(page2_rows)
        assert client._get.call_count == 2

        # First call queries from 15:30:00
        first_call_params = client._get.call_args_list[0][0][2]
        assert first_call_params.get("FID_INPUT_HOUR_1") == "153000"

        # Page 1's earliest bar is 11:30:00, so next page starts 1 minute before: 11:29:00
        second_call_params = client._get.call_args_list[1][0][2]
        assert second_call_params.get("FID_INPUT_HOUR_1") == "112900"
        # No ctx tokens are sent — KIS doesn't use them for intraday
        assert "CTX_AREA_FK100" not in second_call_params
        assert "CTX_AREA_NK100" not in second_call_params


# ---------------------------------------------------------------------------
# 3. Empty output2
# ---------------------------------------------------------------------------

class TestEmptyOutput:
    def test_empty_output2_returns_empty_list(self):
        client = _make_client()
        client._get.return_value = {
            "rt_cd": "0",
            "output2": [],
            "tr_cont": "",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
        }

        with patch("src.brokers.kis.price_client.time"):
            result = fetch_intraday_ohlcv_raw(client, "005930", "20260425")

        assert result == []

    def test_missing_output2_returns_empty_list(self):
        client = _make_client()
        client._get.return_value = {
            "rt_cd": "0",
            "tr_cont": "",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
        }

        with patch("src.brokers.kis.price_client.time"):
            result = fetch_intraday_ohlcv_raw(client, "005930", "20260425")

        assert result == []


# ---------------------------------------------------------------------------
# 4. 429 retry — success after retries
# ---------------------------------------------------------------------------

class TestRateLimitRetrySuccess:
    def test_429_retries_and_succeeds(self):
        client = _make_client()

        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_429.headers = {"Retry-After": "1"}
        http_error_429 = requests.HTTPError(response=mock_429)

        success_response = {
            "rt_cd": "0",
            "output2": [_intraday_row("20260425", "093000")],
            "tr_cont": "",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
        }

        # Two 429s then success, then an empty page to terminate the time-pagination loop
        empty_response = {"rt_cd": "0", "output2": [], "tr_cont": None}
        client._get.side_effect = [http_error_429, http_error_429, success_response, empty_response]

        with patch("src.brokers.kis.price_client.time") as mock_time:
            result = fetch_intraday_ohlcv_raw(client, "005930", "20260425")

        assert len(result) == 1
        # 2 retries + 1 success on page 1 + 1 empty on page 2 = 4 calls
        assert client._get.call_count == 4
        assert mock_time.sleep.call_count >= 2


# ---------------------------------------------------------------------------
# 5. 429 exhausted — raises
# ---------------------------------------------------------------------------

class TestRateLimitRetryExhausted:
    def test_429_exhausted_raises(self):
        client = _make_client()

        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_429.headers = {}
        http_error_429 = requests.HTTPError(response=mock_429)

        client._get.side_effect = http_error_429

        with patch("src.brokers.kis.price_client.time"):
            with pytest.raises(requests.HTTPError):
                fetch_intraday_ohlcv_raw(client, "005930", "20260425")


# ---------------------------------------------------------------------------
# 6. Sleep 0.5s between pages
# ---------------------------------------------------------------------------

class TestSleepBetweenPages:
    def test_rate_limit_sleep_called_between_pages(self):
        client = _make_client()

        # Page 1: 15:00 (one bar). Page 2: 09:30 (terminal — earliest is 09:30 > 09:00, but next call returns empty).
        page1 = {"rt_cd": "0", "output2": [_intraday_row("20260425", "150000")], "tr_cont": None}
        page2 = {"rt_cd": "0", "output2": [_intraday_row("20260425", "093000")], "tr_cont": None}
        page3_empty = {"rt_cd": "0", "output2": [], "tr_cont": None}
        client._get.side_effect = [page1, page2, page3_empty]

        sleep_calls = []
        with patch("src.brokers.kis.price_client.time") as mock_time:
            mock_time.sleep.side_effect = lambda s: sleep_calls.append(s)
            fetch_intraday_ohlcv_raw(client, "005930", "20260425")

        assert any(abs(s - 0.5) < 0.01 for s in sleep_calls)
