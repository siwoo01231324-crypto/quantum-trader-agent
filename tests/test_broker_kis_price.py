"""Unit tests for src/brokers/kis/price_client.py (T1 Red phase).

All HTTP calls are mocked — no real network access.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, call
import time

import pytest

from src.brokers.kis.schemas import KISDailyBar
from src.brokers.kis.price_client import fetch_daily_ohlcv_raw


def _make_client(paper: bool = True) -> MagicMock:
    client = MagicMock()
    client._paper = paper
    if paper:
        client._base_url = "https://openapivts.koreainvestment.com:29443"
    else:
        client._base_url = "https://openapi.koreainvestment.com:9443"
    return client


def _bar_row(date: str = "20260101") -> dict:
    return {
        "stck_bsop_date": date,
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
    def test_returns_list_of_daily_bars(self):
        client = _make_client()
        rows = [_bar_row("20260101"), _bar_row("20260102"), _bar_row("20260103")]
        client._get.return_value = {
            "rt_cd": "0",
            "output2": rows,
            "tr_cont": "",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
        }

        result = fetch_daily_ohlcv_raw(client, "005930", "20260101", "20260103")

        assert isinstance(result, list)
        assert len(result) == 3
        assert all(isinstance(b, KISDailyBar) for b in result)

    def test_field_values_parsed_correctly(self):
        client = _make_client()
        client._get.return_value = {
            "rt_cd": "0",
            "output2": [_bar_row("20260115")],
            "tr_cont": "",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
        }

        result = fetch_daily_ohlcv_raw(client, "005930", "20260115", "20260115")

        bar = result[0]
        assert bar.date == "20260115"
        assert bar.open == pytest.approx(70000.0)
        assert bar.high == pytest.approx(71000.0)
        assert bar.low == pytest.approx(69500.0)
        assert bar.close == pytest.approx(70500.0)
        assert bar.volume == pytest.approx(1234567.0)

    def test_string_to_float_coercion(self):
        """KISDailyBar must coerce string fields to float."""
        bar = KISDailyBar(
            date="20260101",
            open="70000",
            high="71000",
            low="69000",
            close="70500",
            volume="999999",
            trade_amt="50000000000",
        )
        assert isinstance(bar.open, float)
        assert isinstance(bar.close, float)
        assert bar.open == 70000.0


# ---------------------------------------------------------------------------
# 2. Pagination — two pages (tr_cont continuation)
# ---------------------------------------------------------------------------

class TestPagination:
    def test_two_pages_concatenated(self):
        client = _make_client()

        page1_rows = [_bar_row(f"202601{i:02d}") for i in range(1, 11)]  # 10 bars
        page2_rows = [_bar_row(f"202602{i:02d}") for i in range(1, 6)]   # 5 bars

        # First call returns tr_cont="M" (more pages)
        page1_response = {
            "rt_cd": "0",
            "output2": page1_rows,
            "tr_cont": "M",
            "ctx_area_fk100": "FK_TOKEN_1",
            "ctx_area_nk100": "NK_TOKEN_1",
        }
        # Second call returns tr_cont="" (done)
        page2_response = {
            "rt_cd": "0",
            "output2": page2_rows,
            "tr_cont": "",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
        }

        client._get.side_effect = [page1_response, page2_response]

        with patch("src.brokers.kis.price_client.time") as mock_time:
            result = fetch_daily_ohlcv_raw(client, "005930", "20260101", "20260228")

        assert len(result) == 15
        assert client._get.call_count == 2

    def test_continuation_context_passed_on_second_request(self):
        client = _make_client()

        page1_response = {
            "rt_cd": "0",
            "output2": [_bar_row("20260101")],
            "tr_cont": "F",
            "ctx_area_fk100": "FK_VAL",
            "ctx_area_nk100": "NK_VAL",
        }
        page2_response = {
            "rt_cd": "0",
            "output2": [_bar_row("20260102")],
            "tr_cont": "",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
        }
        client._get.side_effect = [page1_response, page2_response]

        with patch("src.brokers.kis.price_client.time") as mock_time:
            fetch_daily_ohlcv_raw(client, "005930", "20260101", "20260102")

        # Second call must carry continuation tokens
        second_call_params = client._get.call_args_list[1][0][2]  # positional arg 3
        assert second_call_params.get("CTX_AREA_FK100") == "FK_VAL"
        assert second_call_params.get("CTX_AREA_NK100") == "NK_VAL"


# ---------------------------------------------------------------------------
# 3. Empty response
# ---------------------------------------------------------------------------

class TestEmptyResponse:
    def test_empty_output_returns_empty_list(self):
        client = _make_client()
        client._get.return_value = {
            "rt_cd": "0",
            "output2": [],
            "tr_cont": "",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
        }

        result = fetch_daily_ohlcv_raw(client, "005930", "20260101", "20260101")

        assert result == []

    def test_missing_output2_key_returns_empty_list(self):
        client = _make_client()
        client._get.return_value = {
            "rt_cd": "0",
            "tr_cont": "",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
        }

        result = fetch_daily_ohlcv_raw(client, "005930", "20260101", "20260101")

        assert result == []


# ---------------------------------------------------------------------------
# 4. 429 rate-limit retry
# ---------------------------------------------------------------------------

class TestRateLimitRetry:
    def test_429_retries_with_backoff_and_succeeds(self):
        """On 429, retry up to 3 times using Retry-After or exponential backoff."""
        import requests as req_lib

        client = _make_client()

        # Build a 429 response mock
        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_429.headers = {"Retry-After": "1"}
        http_error_429 = req_lib.HTTPError(response=mock_429)

        success_response = {
            "rt_cd": "0",
            "output2": [_bar_row("20260101")],
            "tr_cont": "",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
        }

        # First two attempts raise 429, third succeeds
        client._get.side_effect = [
            http_error_429,
            http_error_429,
            success_response,
        ]

        with patch("src.brokers.kis.price_client.time") as mock_time:
            result = fetch_daily_ohlcv_raw(client, "005930", "20260101", "20260101")

        assert len(result) == 1
        assert client._get.call_count == 3
        # time.sleep must have been called for backoff
        assert mock_time.sleep.call_count >= 2

    def test_429_exhausted_raises(self):
        """After max retries on 429, exception bubbles up."""
        import requests as req_lib

        client = _make_client()

        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_429.headers = {}
        http_error_429 = req_lib.HTTPError(response=mock_429)

        client._get.side_effect = http_error_429

        with patch("src.brokers.kis.price_client.time"):
            with pytest.raises(req_lib.HTTPError):
                fetch_daily_ohlcv_raw(client, "005930", "20260101", "20260101")

    def test_rate_limit_sleep_between_pages(self):
        """0.5s sleep must be called between paginated requests."""
        client = _make_client()

        page1 = {
            "rt_cd": "0",
            "output2": [_bar_row("20260101")],
            "tr_cont": "M",
            "ctx_area_fk100": "FK",
            "ctx_area_nk100": "NK",
        }
        page2 = {
            "rt_cd": "0",
            "output2": [_bar_row("20260102")],
            "tr_cont": "",
            "ctx_area_fk100": "",
            "ctx_area_nk100": "",
        }
        client._get.side_effect = [page1, page2]

        sleep_calls = []
        with patch("src.brokers.kis.price_client.time") as mock_time:
            mock_time.sleep.side_effect = lambda s: sleep_calls.append(s)
            fetch_daily_ohlcv_raw(client, "005930", "20260101", "20260102")

        # At least one sleep(0.5) between pages
        assert any(abs(s - 0.5) < 0.01 for s in sleep_calls)
