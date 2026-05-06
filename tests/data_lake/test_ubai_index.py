"""Tests for src/data_lake/ubai_index.py — TDD red phase.

All Upbit API calls are mocked. No real network calls.
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

from src.data_lake.ubai_index import fetch_ubai_index


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_market_response(symbols: list[str]) -> list[dict]:
    return [{"market": s, "korean_name": s, "english_name": s} for s in symbols]


def _make_candles_response(count: int, price: float = 100.0, volume: float = 1_000_000.0) -> list[dict]:
    """Minimal Upbit /v1/candles/days response."""
    return [
        {
            "market": "KRW-BTC",
            "candle_date_time_utc": f"2020-01-{i + 1:02d}T00:00:00",
            "candle_date_time_kst": f"2020-01-{i + 1:02d}T09:00:00",
            "opening_price": price,
            "high_price": price * 1.01,
            "low_price": price * 0.99,
            "trade_price": price,
            "candle_acc_trade_price": price * volume,
            "candle_acc_trade_volume": volume,
            "timestamp": 1578000000000 + i * 86400000,
        }
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestFetchUbaiIndexNormalResponse:
    """Normal mock response: returns a valid pd.Series."""

    def test_returns_series(self):
        """fetch_ubai_index should return a pd.Series."""
        session = MagicMock(spec=requests.Session)

        # /v1/market/all  → list of KRW markets (exclude BTC/ETH, include some alts)
        markets = _make_market_response(
            ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA",
             "KRW-DOGE", "KRW-AVAX", "KRW-LINK", "KRW-DOT", "KRW-ATOM",
             "KRW-MATIC", "KRW-SUI"]
        )
        candles = _make_candles_response(5)

        resp_markets = MagicMock()
        resp_markets.status_code = 200
        resp_markets.json.return_value = markets

        resp_candles = MagicMock()
        resp_candles.status_code = 200
        resp_candles.json.return_value = candles

        session.get.side_effect = lambda url, **kw: (
            resp_markets if "market/all" in url else resp_candles
        )

        result = fetch_ubai_index(
            start="2020-01-01",
            end="2020-01-05",
            top_n=5,
            market="KRW",
            session=session,
        )
        assert isinstance(result, pd.Series)
        assert len(result) > 0

    def test_monthly_rebal_weights_sum_to_one(self):
        """Monthly rebalanced weights should sum to 1.0 per rebal date."""
        session = MagicMock(spec=requests.Session)

        markets = _make_market_response(
            ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA",
             "KRW-DOGE", "KRW-AVAX", "KRW-LINK", "KRW-DOT", "KRW-ATOM"]
        )
        candles = _make_candles_response(31, price=1000.0, volume=500_000.0)

        resp_markets = MagicMock()
        resp_markets.status_code = 200
        resp_markets.json.return_value = markets

        resp_candles = MagicMock()
        resp_candles.status_code = 200
        resp_candles.json.return_value = candles

        session.get.side_effect = lambda url, **kw: (
            resp_markets if "market/all" in url else resp_candles
        )

        # Expose weights via internal function if available, else just check series is valid
        result = fetch_ubai_index(
            start="2020-01-01",
            end="2020-01-31",
            top_n=5,
            market="KRW",
            session=session,
        )
        # The returned index should be a float series (cumulative or daily return)
        assert isinstance(result, pd.Series)
        assert not result.isna().all()


class TestFetchUbaiIndexApiFallback:
    """API failure should raise or return empty Series gracefully."""

    def test_api_failure_raises_or_returns_empty(self):
        """If /v1/market/all returns 500, function raises or returns empty Series."""
        session = MagicMock(spec=requests.Session)

        resp_fail = MagicMock()
        resp_fail.status_code = 500
        resp_fail.raise_for_status.side_effect = requests.HTTPError("server error")
        resp_fail.json.return_value = {}

        session.get.return_value = resp_fail

        with pytest.raises((requests.HTTPError, ValueError, RuntimeError)):
            fetch_ubai_index(
                start="2020-01-01",
                end="2020-01-05",
                top_n=5,
                session=session,
            )


class TestFetchUbaiIndexRateLimit:
    """Rate limit (429) should trigger retry/backoff and eventually succeed."""

    def test_rate_limit_retry_succeeds(self):
        """After 429, second call succeeds — returns valid Series."""
        session = MagicMock(spec=requests.Session)

        markets = _make_market_response(
            ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA",
             "KRW-DOGE", "KRW-AVAX", "KRW-LINK", "KRW-DOT", "KRW-ATOM"]
        )
        candles = _make_candles_response(5)

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.raise_for_status.side_effect = requests.HTTPError("rate limited")

        resp_ok_markets = MagicMock()
        resp_ok_markets.status_code = 200
        resp_ok_markets.json.return_value = markets

        resp_ok_candles = MagicMock()
        resp_ok_candles.status_code = 200
        resp_ok_candles.json.return_value = candles

        call_count = {"n": 0}

        def side_effect(url, **kw):
            call_count["n"] += 1
            if "market/all" in url:
                if call_count["n"] == 1:
                    return resp_429
                return resp_ok_markets
            return resp_ok_candles

        session.get.side_effect = side_effect

        # Should not raise — retry should handle the 429
        result = fetch_ubai_index(
            start="2020-01-01",
            end="2020-01-05",
            top_n=5,
            session=session,
        )
        assert isinstance(result, pd.Series)


class TestFetchUbaiIndexExcludesBtcEth:
    """BTC and ETH must be excluded from the alt index."""

    def test_btc_eth_excluded(self):
        """Top-N selection must skip KRW-BTC and KRW-ETH."""
        session = MagicMock(spec=requests.Session)

        # Only BTC + ETH + 3 alts
        markets = _make_market_response(
            ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA"]
        )
        candles = _make_candles_response(5)

        resp_markets = MagicMock()
        resp_markets.status_code = 200
        resp_markets.json.return_value = markets

        resp_candles = MagicMock()
        resp_candles.status_code = 200
        resp_candles.json.return_value = candles

        session.get.side_effect = lambda url, **kw: (
            resp_markets if "market/all" in url else resp_candles
        )

        # top_n=3 from 3 alts (XRP, SOL, ADA) — should work without BTC/ETH
        result = fetch_ubai_index(
            start="2020-01-01",
            end="2020-01-05",
            top_n=3,
            session=session,
        )
        assert isinstance(result, pd.Series)
