from __future__ import annotations
import json
import pytest
from decimal import Decimal

from src.live.feed import BinancePublicFeed
from src.live.types import Tick


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _aggtrade_msg(
    symbol: str = "BTCUSDT",
    price: str = "30000.50",
    qty: str = "0.001",
    server_ts_ms: int = 1_700_000_000_000,
) -> dict:
    return {
        "e": "aggTrade",
        "E": server_ts_ms,
        "s": symbol,
        "p": price,
        "q": qty,
        "f": 1,
        "l": 1,
        "T": server_ts_ms,
        "m": False,
    }


class FakeWS:
    def __init__(self, messages: list):
        self._msgs = list(messages)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for m in self._msgs:
            yield json.dumps(m) if not isinstance(m, str) else m

    async def close(self):
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_binance_public_feed_url_format():
    feed = BinancePublicFeed(["BTCUSDT"])
    assert feed.BASE_URL == "wss://fstream.binance.com/ws"


@pytest.mark.asyncio
async def test_message_parsing():
    msg = _aggtrade_msg(symbol="BTCUSDT", price="30000.50", qty="0.001", server_ts_ms=1_700_000_000_000)
    feed = BinancePublicFeed(["BTCUSDT"])
    feed._ws = FakeWS([msg])

    ticks = []
    async for tick in feed:
        ticks.append(tick)

    assert len(ticks) == 1
    tick = ticks[0]
    assert tick.symbol == "BTCUSDT"
    assert tick.price == Decimal("30000.50")
    assert tick.qty == Decimal("0.001")
    assert tick.server_ts is not None
    assert "2023" in tick.server_ts  # 1_700_000_000_000 ms → 2023-11-xx
    assert tick.ts is not None


@pytest.mark.asyncio
async def test_decimal_no_float():
    msg = _aggtrade_msg(price="12345.6789", qty="0.00123456")
    feed = BinancePublicFeed(["BTCUSDT"])
    feed._ws = FakeWS([msg])

    ticks = []
    async for tick in feed:
        ticks.append(tick)

    assert len(ticks) == 1
    assert isinstance(ticks[0].price, Decimal)
    assert isinstance(ticks[0].qty, Decimal)


@pytest.mark.asyncio
async def test_aclose_idempotent():
    feed = BinancePublicFeed(["BTCUSDT"])
    feed._ws = FakeWS([])
    # First close
    await feed.aclose()
    # Second close — must not raise
    await feed.aclose()


@pytest.mark.asyncio
async def test_invalid_json_skipped():
    valid_msg = _aggtrade_msg(price="50000.00", qty="0.01")
    invalid_msg = {"invalid": "msg"}  # missing "e" key → skipped (e != aggTrade)
    feed = BinancePublicFeed(["BTCUSDT"])
    feed._ws = FakeWS([invalid_msg, valid_msg])

    ticks = []
    async for tick in feed:
        ticks.append(tick)

    # invalid_msg has no "e"=="aggTrade" so it is skipped; only valid_msg yields a tick
    assert len(ticks) == 1
    assert ticks[0].price == Decimal("50000.00")
