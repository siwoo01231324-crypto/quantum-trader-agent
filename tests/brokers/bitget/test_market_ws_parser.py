"""Unit tests for src.brokers.bitget.market_ws.parse_message."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.brokers.bitget.market_ws import KlineEvent, MarkPriceEvent, parse_message


def _kline_msg(symbol: str = "BTCUSDT", interval: str = "1h", rows: list | None = None) -> dict:
    return {
        "action": "update",
        "arg": {
            "instType": "USDT-FUTURES",
            "channel": f"candle{interval.replace('h','H').replace('d','D')}",
            "instId": symbol,
        },
        "data": rows or [
            # [ts, open, high, low, close, baseVol, quoteVol]
            ["1700000000000", "50000", "50500", "49800", "50200", "100.5", "5025000"],
        ],
    }


def _ticker_msg(symbols: list[str] | None = None) -> dict:
    if symbols is None:
        symbols = ["BTCUSDT"]
    data = [
        {"instId": s, "markPrice": "50100.5", "ts": "1700000000000", "lastPr": "50050"}
        for s in symbols
    ]
    return {
        "action": "snapshot",
        "arg": {"instType": "USDT-FUTURES", "channel": "ticker", "instId": symbols[0]},
        "data": data,
    }


def test_parse_kline_single_row():
    events = parse_message(_kline_msg())
    assert events is not None
    assert len(events) == 1
    e = events[0]
    assert isinstance(e, KlineEvent)
    assert e.symbol == "BTCUSDT"
    assert e.interval == "1h"
    assert e.open == Decimal("50000")
    assert e.close == Decimal("50200")
    assert e.high == Decimal("50500")
    assert e.low == Decimal("49800")
    # Last row in update = still-forming bar → closed=False
    assert e.closed is False


def test_parse_kline_multi_row_marks_earlier_as_closed():
    rows = [
        ["1700000000000", "50000", "50500", "49800", "50200", "100", "5000000"],
        ["1700003600000", "50200", "50300", "50100", "50250", "50", "2500000"],
    ]
    events = parse_message(_kline_msg(rows=rows))
    assert events is not None
    assert len(events) == 2
    # First closed, last still forming.
    assert events[0].closed is True
    assert events[1].closed is False


def test_parse_kline_snapshot_all_closed():
    msg = _kline_msg()
    msg["action"] = "snapshot"
    events = parse_message(msg)
    assert events[0].closed is True


def test_parse_ticker_extracts_mark_price():
    events = parse_message(_ticker_msg(["BTCUSDT", "ETHUSDT"]))
    assert events is not None
    assert len(events) == 2
    assert all(isinstance(e, MarkPriceEvent) for e in events)
    assert events[0].symbol == "BTCUSDT"
    assert events[0].mark_price == Decimal("50100.5")


def test_parse_ticker_skips_rows_without_mark_price():
    msg = _ticker_msg(["BTCUSDT"])
    msg["data"][0].pop("markPrice")
    assert parse_message(msg) is None


def test_parse_returns_none_for_subscribe_ack():
    msg = {"event": "subscribe", "arg": {"channel": "ticker", "instId": "BTCUSDT"}}
    assert parse_message(msg) is None


def test_parse_returns_none_for_unknown_channel():
    msg = {"arg": {"channel": "trade", "instId": "BTCUSDT"}, "data": [{"px": "50000"}]}
    assert parse_message(msg) is None


def test_parse_returns_none_for_empty_data():
    msg = {"arg": {"channel": "candle1H", "instId": "BTCUSDT"}, "data": []}
    assert parse_message(msg) is None


@pytest.mark.parametrize("interval,ms", [
    ("1m", 60_000),
    ("5m", 300_000),
    ("1h", 3_600_000),
    ("1d", 86_400_000),
])
def test_kline_close_time_derived_from_interval(interval: str, ms: int):
    events = parse_message(_kline_msg(interval=interval))
    assert events is not None
    e = events[0]
    assert e.close_time == e.open_time + ms - 1
