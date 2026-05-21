"""Unit tests for brokers.binance.market_ws — pure parser + URL builder + REST fetch."""
from __future__ import annotations

import httpx
import pytest
import respx

from brokers.binance.market_ws import (
    KlineEvent,
    MarkPriceEvent,
    REST_BASE_LIVE,
    WS_BASE_LIVE,
    bootstrap_history,
    build_combined_stream_url,
    fetch_klines_rest,
    parse_combined_message,
)


# -----------------------------------------------------------------------------
# parse_combined_message
# -----------------------------------------------------------------------------

def test_parse_kline_event():
    msg = {
        "stream": "btcusdt@kline_1h",
        "data": {
            "e": "kline", "E": 1700000000000, "s": "BTCUSDT",
            "k": {
                "t": 1700000000000, "T": 1700003599999, "s": "BTCUSDT",
                "i": "1h", "o": "55000.0", "c": "55500.0",
                "h": "55600.0", "l": "54800.0", "v": "1234.5",
                "x": True,
            },
        },
    }
    ev = parse_combined_message(msg)
    assert isinstance(ev, KlineEvent)
    assert ev.symbol == "BTCUSDT"
    assert ev.interval == "1h"
    assert ev.open == 55000.0
    assert ev.close == 55500.0
    assert ev.is_closed is True


def test_parse_kline_unconfirmed_bar():
    msg = {
        "stream": "btcusdt@kline_5m",
        "data": {
            "e": "kline", "E": 1700000000000, "s": "BTCUSDT",
            "k": {
                "t": 0, "T": 0, "s": "BTCUSDT", "i": "5m",
                "o": "1", "c": "1", "h": "1", "l": "1", "v": "0",
                "x": False,
            },
        },
    }
    ev = parse_combined_message(msg)
    assert isinstance(ev, KlineEvent)
    assert ev.is_closed is False


def test_parse_mark_price_arr_returns_list():
    msg = {
        "stream": "!markPrice@arr@1s",
        "data": [
            {"e": "markPriceUpdate", "E": 1, "s": "BTCUSDT",
             "p": "55050.5", "r": "0.0001", "T": 1700004000000},
            {"e": "markPriceUpdate", "E": 1, "s": "ETHUSDT",
             "p": "3500.0", "r": "-0.0002", "T": 1700004000000},
        ],
    }
    out = parse_combined_message(msg)
    assert isinstance(out, list)
    assert len(out) == 2
    assert isinstance(out[0], MarkPriceEvent)
    assert out[0].symbol == "BTCUSDT"
    assert out[0].mark_price == pytest.approx(55050.5)
    assert out[1].funding_rate == pytest.approx(-0.0002)


def test_parse_returns_none_for_unknown_event_type():
    msg = {"stream": "btcusdt@aggTrade", "data": {"e": "aggTrade", "s": "BTCUSDT"}}
    assert parse_combined_message(msg) is None


def test_parse_returns_none_for_malformed_kline():
    msg = {"stream": "btcusdt@kline_1h", "data": {"e": "kline", "s": "BTCUSDT", "k": {}}}
    assert parse_combined_message(msg) is None


def test_parse_mark_price_skips_malformed_entries():
    msg = {
        "stream": "!markPrice@arr@1s",
        "data": [
            {"e": "markPriceUpdate", "s": "BTCUSDT", "p": "55000"},
            {"e": "markPriceUpdate", "s": "BAD", "p": "not-a-number"},
            {"e": "otherEvent", "s": "ETHUSDT", "p": "3500"},
        ],
    }
    out = parse_combined_message(msg)
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0].symbol == "BTCUSDT"


# -----------------------------------------------------------------------------
# build_combined_stream_url
# -----------------------------------------------------------------------------

def test_build_url_lowers_symbols_and_includes_mark_price():
    url = build_combined_stream_url(
        WS_BASE_LIVE,
        symbols=["BTCUSDT", "ETHUSDT"],
        intervals=["1h", "5m"],
        include_mark_price_arr=True,
    )
    assert url.startswith(f"{WS_BASE_LIVE}/stream?streams=")
    assert "btcusdt@kline_1h" in url
    assert "ethusdt@kline_5m" in url
    assert "!markPrice@arr@1s" in url
    # No uppercase symbol in stream names
    assert "BTCUSDT@kline" not in url


def test_build_url_without_mark_price():
    url = build_combined_stream_url(
        WS_BASE_LIVE, symbols=["BTCUSDT"], intervals=["1h"],
        include_mark_price_arr=False,
    )
    assert "!markPrice@arr@1s" not in url


# -----------------------------------------------------------------------------
# fetch_klines_rest
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_fetch_klines_parses_response():
    rows = [
        # [openTime, open, high, low, close, volume, closeTime, ...]
        [1700000000000, "55000", "55100", "54900", "55050", "10.5",
         1700003599999, "578025", 100, "5", "275000", "0"],
        [1700003600000, "55050", "55200", "55000", "55150", "8.2",
         1700007199999, "452230", 80, "4", "226115", "0"],
    ]
    respx.get(f"{REST_BASE_LIVE}/fapi/v1/klines").mock(
        return_value=httpx.Response(200, json=rows)
    )
    df = await fetch_klines_rest(symbol="BTCUSDT", interval="1h", limit=2)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2
    assert df["open"].iloc[0] == 55000.0
    assert df["close"].iloc[1] == 55150.0
    assert df.index.tz is not None  # UTC index


@pytest.mark.asyncio
@respx.mock
async def test_fetch_klines_handles_empty_response():
    respx.get(f"{REST_BASE_LIVE}/fapi/v1/klines").mock(
        return_value=httpx.Response(200, json=[])
    )
    df = await fetch_klines_rest(symbol="BTCUSDT", interval="1h")
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


@pytest.mark.asyncio
@respx.mock
async def test_bootstrap_history_groups_by_symbol_and_interval():
    rows_1h = [[1700000000000, "55000", "55100", "54900", "55050", "10",
                1700003599999, "0", 0, "0", "0", "0"]]
    rows_5m = [[1700000000000, "55000", "55050", "54950", "55020", "1",
                1700000299999, "0", 0, "0", "0", "0"]]
    respx.get(f"{REST_BASE_LIVE}/fapi/v1/klines",
              params={"symbol": "BTCUSDT", "interval": "1h", "limit": "100"}).mock(
        return_value=httpx.Response(200, json=rows_1h))
    respx.get(f"{REST_BASE_LIVE}/fapi/v1/klines",
              params={"symbol": "BTCUSDT", "interval": "5m", "limit": "100"}).mock(
        return_value=httpx.Response(200, json=rows_5m))

    out = await bootstrap_history(symbols=["BTCUSDT"], intervals=("1h", "5m"))
    assert "BTCUSDT" in out
    assert set(out["BTCUSDT"].keys()) == {"1h", "5m"}
    assert out["BTCUSDT"]["1h"]["close"].iloc[0] == 55050.0
    assert out["BTCUSDT"]["5m"]["close"].iloc[0] == 55020.0
