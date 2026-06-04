"""Unit tests for src.live.feed.BitgetPublicFeed + BitgetMarkPriceFeed (P4b).

WS connection NOT exercised — instead we directly drive the parser path by
injecting a mock async-iterable into ``_ws`` after construction. Verifies:
  - DEMO_DEMO/LIVE constants resolve via paper flag
  - Tick parsing from trade-channel rows
  - markPrice batch parsing from ticker-channel rows
  - Empty / unknown-channel frames yield nothing
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.live.feed import BitgetMarkPriceFeed, BitgetPublicFeed


class _MockWS:
    def __init__(self, frames: list[str]) -> None:
        self._frames = list(frames)

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for f in self._frames:
            yield f

    async def close(self) -> None:
        pass


def _trade_msg(symbol: str, price: str, qty: str, ts_ms: int) -> str:
    return json.dumps({
        "action": "update",
        "arg": {"instType": "USDT-FUTURES", "channel": "trade", "instId": symbol},
        "data": [[str(ts_ms), price, qty, "buy"]],
    })


def _ticker_msg(symbol: str, mark: str, last: str, ts_ms: int) -> str:
    return json.dumps({
        "action": "snapshot",
        "arg": {"instType": "USDT-FUTURES", "channel": "ticker", "instId": symbol},
        "data": [{"instId": symbol, "markPrice": mark, "lastPr": last, "ts": str(ts_ms)}],
    })


# ── BitgetPublicFeed ──────────────────────────────────────────────────────────


def test_public_feed_default_url_demo_vs_live():
    demo = BitgetPublicFeed(["BTCUSDT"], paper=True)
    live = BitgetPublicFeed(["BTCUSDT"], paper=False)
    assert "wspap" in demo._base_url
    assert "wspap" not in live._base_url


@pytest.mark.asyncio
async def test_public_feed_yields_ticks_from_trade_channel():
    feed = BitgetPublicFeed(["BTCUSDT"], paper=True)
    feed._ws = _MockWS([
        _trade_msg("BTCUSDT", "67500.5", "0.001", 1780000000000),
        _trade_msg("BTCUSDT", "67501.0", "0.002", 1780000000100),
    ])
    ticks: list = []
    async for t in feed:
        ticks.append(t)
        if len(ticks) >= 2:
            break
    assert len(ticks) == 2
    assert ticks[0].symbol == "BTCUSDT"
    assert ticks[0].price == Decimal("67500.5")
    assert ticks[0].qty == Decimal("0.001")
    assert ticks[1].price == Decimal("67501.0")


@pytest.mark.asyncio
async def test_public_feed_handles_dict_shape_trade_rows():
    # 2026-06-05 — 운영 docker logs 에서 Bitget v2 trade channel 이 dict 형식
    # 로 push → producer KeyError: 0 무한 재접속. 양 형식 모두 지원해야 함.
    feed = BitgetPublicFeed(["BTCUSDT"], paper=True)
    msg = json.dumps({
        "action": "update",
        "arg": {"instType": "USDT-FUTURES", "channel": "trade", "instId": "BTCUSDT"},
        "data": [{"ts": "1780000000000", "price": "67500.5", "size": "0.005", "side": "sell"}],
    })
    feed._ws = _MockWS([msg])
    ticks: list = []
    async for t in feed:
        ticks.append(t)
        if ticks:
            break
    assert len(ticks) == 1
    assert ticks[0].price == Decimal("67500.5")
    assert ticks[0].qty == Decimal("0.005")


@pytest.mark.asyncio
async def test_public_feed_skips_non_trade_channel():
    feed = BitgetPublicFeed(["BTCUSDT"], paper=True)
    feed._ws = _MockWS([
        _ticker_msg("BTCUSDT", "67500", "67499", 1780000000000),  # wrong channel
        _trade_msg("BTCUSDT", "67500", "0.001", 1780000000000),
    ])
    ticks: list = []
    async for t in feed:
        ticks.append(t)
        if len(ticks) >= 1:
            break
    assert len(ticks) == 1
    assert ticks[0].price == Decimal("67500")


# ── BitgetMarkPriceFeed ───────────────────────────────────────────────────────


def test_markprice_feed_default_url_demo_vs_live():
    demo = BitgetMarkPriceFeed(["BTCUSDT"], paper=True)
    live = BitgetMarkPriceFeed(["BTCUSDT"], paper=False)
    assert "wspap" in demo._base_url
    assert "wspap" not in live._base_url


@pytest.mark.asyncio
async def test_markprice_feed_yields_batch_tuples_from_ticker():
    feed = BitgetMarkPriceFeed(["BTCUSDT", "ETHUSDT"], paper=True)
    feed._ws = _MockWS([
        _ticker_msg("BTCUSDT", "67500.5", "67499", 1780000000000),
        _ticker_msg("ETHUSDT", "1850.25", "1850.20", 1780000000100),
    ])
    batches: list = []
    async for b in feed:
        batches.append(b)
        if len(batches) >= 2:
            break
    assert len(batches) == 2
    # Each batch is [(symbol, Decimal, datetime)]
    assert batches[0][0][0] == "BTCUSDT"
    assert batches[0][0][1] == Decimal("67500.5")
    assert batches[1][0][0] == "ETHUSDT"
    assert batches[1][0][1] == Decimal("1850.25")


@pytest.mark.asyncio
async def test_markprice_feed_skips_rows_without_markprice():
    feed = BitgetMarkPriceFeed(["BTCUSDT"], paper=True)
    # ticker frame WITHOUT markPrice — should yield nothing
    msg = json.dumps({
        "action": "snapshot",
        "arg": {"instType": "USDT-FUTURES", "channel": "ticker", "instId": "BTCUSDT"},
        "data": [{"instId": "BTCUSDT", "lastPr": "67500", "ts": "1780000000000"}],
    })
    feed._ws = _MockWS([msg])
    batches: list = []
    async for b in feed:
        batches.append(b)
    assert batches == []


@pytest.mark.asyncio
async def test_markprice_feed_skips_non_ticker_channel():
    feed = BitgetMarkPriceFeed(["BTCUSDT"], paper=True)
    feed._ws = _MockWS([
        _trade_msg("BTCUSDT", "67500", "0.001", 1780000000000),  # wrong channel
        _ticker_msg("BTCUSDT", "67500.5", "67499", 1780000000100),
    ])
    batches: list = []
    async for b in feed:
        batches.append(b)
        if batches:
            break
    assert len(batches) == 1
    assert batches[0][0][1] == Decimal("67500.5")
