"""Tests for ``BinanceMarkPriceFeed`` + ``_run_mark_price_consumer``.

Validates the multi-symbol mark-price path that wires ``!markPrice@arr@1s``
into ``LivePositionRiskManager.evaluate`` for every USDT-perp symbol. The
fix unblocks universe-scanner stop/TP auto-exits — without it, the legacy
single-symbol aggTrade feed only evaluated one symbol per tick (#238
follow-up).
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

import pytest

from src.live.feed import BinanceMarkPriceFeed


class _FakeWS:
    """Minimal async-iterable WS stub."""

    def __init__(self, frames: list[str]) -> None:
        self._frames = frames
        self.closed = False

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for frame in self._frames:
            yield frame

    async def close(self) -> None:
        self.closed = True


def _mark_payload(symbol: str, price: str, event_ts_ms: int = 1_700_000_000_000) -> dict:
    return {
        "e": "markPriceUpdate",
        "E": event_ts_ms,
        "s": symbol,
        "p": price,
        "i": price,
        "P": price,
        "r": "0.0001",
        "T": event_ts_ms + 1000,
    }


@pytest.mark.asyncio
async def test_mark_price_feed_yields_batched_decimal_prices() -> None:
    feed = BinanceMarkPriceFeed(base_url="wss://stream.binancefuture.com/ws")
    payload = [
        _mark_payload("BTCUSDT", "30000.5"),
        _mark_payload("ETHUSDT", "1800.25"),
        _mark_payload("NEARUSDT", "4.321"),
    ]
    feed._ws = _FakeWS([json.dumps(payload)])

    batches: list[Any] = []
    async for batch in feed:
        batches.append(batch)

    assert len(batches) == 1
    batch = batches[0]
    assert [item[0] for item in batch] == ["BTCUSDT", "ETHUSDT", "NEARUSDT"]
    assert [item[1] for item in batch] == [
        Decimal("30000.5"), Decimal("1800.25"), Decimal("4.321"),
    ]
    for _, _, ts in batch:
        assert ts.tzinfo is not None  # tz-aware UTC


@pytest.mark.asyncio
async def test_mark_price_feed_skips_non_mark_events() -> None:
    """Non-markPriceUpdate events in the array must be silently dropped."""
    feed = BinanceMarkPriceFeed(base_url="wss://stream.binancefuture.com/ws")
    payload = [
        {"e": "trade", "s": "BTCUSDT", "p": "30000"},  # wrong event type
        _mark_payload("ETHUSDT", "1800"),
        {"not": "a dict event"},  # malformed
    ]
    feed._ws = _FakeWS([json.dumps(payload)])

    batches = []
    async for batch in feed:
        batches.append(batch)

    assert len(batches) == 1
    assert [b[0] for b in batches[0]] == ["ETHUSDT"]


@pytest.mark.asyncio
async def test_mark_price_feed_skips_non_array_messages() -> None:
    """Listener-key error frames (dicts) must not crash the iterator."""
    feed = BinanceMarkPriceFeed(base_url="wss://stream.binancefuture.com/ws")
    feed._ws = _FakeWS([
        json.dumps({"error": "listenKey expired"}),
        json.dumps([_mark_payload("BTCUSDT", "30000")]),
    ])

    batches = []
    async for batch in feed:
        batches.append(batch)

    assert len(batches) == 1
    assert batches[0][0][0] == "BTCUSDT"


@pytest.mark.asyncio
async def test_mark_price_feed_skips_malformed_json() -> None:
    feed = BinanceMarkPriceFeed(base_url="wss://stream.binancefuture.com/ws")
    feed._ws = _FakeWS([
        "not json at all",
        json.dumps([_mark_payload("BTCUSDT", "30000")]),
    ])

    batches = []
    async for batch in feed:
        batches.append(batch)

    assert len(batches) == 1
    assert batches[0][0][0] == "BTCUSDT"


def test_mark_price_feed_default_testnet_url() -> None:
    """Testnet endpoint is the production injection — protects against
    Korean-IP regional restriction on the mainnet ``fstream`` host."""
    feed = BinanceMarkPriceFeed(base_url=BinanceMarkPriceFeed.DEFAULT_TESTNET)
    assert feed._base_url == "wss://stream.binancefuture.com/ws"


def test_mark_price_feed_env_url_normalised(monkeypatch) -> None:
    """``BINANCE_WS_BASE_URL`` env should auto-append ``/ws`` if missing."""
    monkeypatch.setenv("BINANCE_WS_BASE_URL", "wss://stream.binancefuture.com")
    feed = BinanceMarkPriceFeed()
    assert feed._base_url == "wss://stream.binancefuture.com/ws"

    monkeypatch.setenv("BINANCE_WS_BASE_URL", "wss://stream.binancefuture.com/ws")
    feed2 = BinanceMarkPriceFeed()
    assert feed2._base_url == "wss://stream.binancefuture.com/ws"
