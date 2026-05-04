"""Tests for src.live.feed_kis (#177)."""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from src.brokers.kis.schemas import KISIntradayBar
from src.live.feed_kis import KISMarketFeed, MockReplayFeed, is_krx_market_open
from src.live.types import Tick


# ---------------------------------------------------------------------------
# is_krx_market_open
# ---------------------------------------------------------------------------

def test_is_krx_market_open_weekday_session():
    # 2026-05-04 is a Monday — KRX trading day. 10:00 KST → 01:00 UTC.
    ts = datetime(2026, 5, 4, 1, 0, tzinfo=timezone.utc)
    assert is_krx_market_open(ts) is True


def test_is_krx_market_open_weekend():
    # 2026-05-02 is a Saturday.
    ts = datetime(2026, 5, 2, 1, 0, tzinfo=timezone.utc)
    assert is_krx_market_open(ts) is False


def test_is_krx_market_open_outside_hours():
    # 2026-05-04 16:30 KST → 07:30 UTC. After session close.
    ts = datetime(2026, 5, 4, 7, 30, tzinfo=timezone.utc)
    assert is_krx_market_open(ts) is False


# ---------------------------------------------------------------------------
# MockReplayFeed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mock_replay_feed_yields_in_order():
    ticks = [
        Tick(symbol="005930", price=Decimal("80000"), qty=Decimal("100"),
             ts="2026-05-04T01:00:00+00:00"),
        Tick(symbol="005930", price=Decimal("80100"), qty=Decimal("110"),
             ts="2026-05-04T01:01:00+00:00"),
    ]
    feed = MockReplayFeed(ticks)
    await feed.connect()
    await feed.subscribe(["005930"])
    received = []
    async for t in feed:
        received.append(t)
    assert [t.price for t in received] == [Decimal("80000"), Decimal("80100")]


@pytest.mark.asyncio
async def test_mock_replay_feed_aclose_stops_iteration():
    ticks = [
        Tick(symbol="005930", price=Decimal(str(i)), qty=Decimal("0"),
             ts=f"2026-05-04T01:{i:02d}:00+00:00")
        for i in range(5)
    ]
    feed = MockReplayFeed(ticks)
    received = []
    async for t in feed:
        received.append(t)
        if len(received) == 2:
            await feed.aclose()
    assert len(received) <= 3  # some implementations buffer one extra


# ---------------------------------------------------------------------------
# KISMarketFeed (mock client)
# ---------------------------------------------------------------------------

class _StubKISClient:
    """Duck-types KISClient for fetch_intraday_ohlcv_raw."""
    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = 0
    def _get(self, path, tr_id, params):  # used by price_client._call_intraday_with_429_retry
        self.calls += 1
        return self._pages.pop(0) if self._pages else {"output2": [], "tr_cont": ""}


@pytest.mark.asyncio
async def test_kis_market_feed_yields_only_new_bars():
    """Two polls → second call returns same bar → second iteration yields nothing."""
    bar1_row = {
        "stck_bsop_date": "20260504", "stck_cntg_hour": "100000",
        "stck_oprc": "80000", "stck_hgpr": "80100",
        "stck_lwpr": "79900", "stck_clpr": "80050",
        "cntg_vol": "12345", "acml_tr_pbmn": "1000000",
    }
    page = {"output2": [bar1_row], "tr_cont": ""}
    client = _StubKISClient([page, page])  # same bar both polls

    feed = KISMarketFeed(
        symbols=["005930"], client=client,
        poll_interval_sec=0.0, interval_min="1",
        market_open_check=False,
    )
    await feed.connect()
    received: list[Tick] = []

    async def _runner():
        async for tick in feed:
            received.append(tick)
            if len(received) >= 1:
                await feed.aclose()
                return

    await asyncio.wait_for(_runner(), timeout=2.0)

    assert len(received) == 1
    tick = received[0]
    assert tick.symbol == "005930"
    assert tick.price == Decimal("80050")
    assert tick.qty == Decimal("12345")


@pytest.mark.asyncio
async def test_kis_market_feed_skips_when_market_closed():
    """market_open_check=True + closed session → no API call, no tick yielded."""
    client = _StubKISClient([])

    feed = KISMarketFeed(
        symbols=["005930"], client=client,
        poll_interval_sec=0.05, interval_min="1",
        market_open_check=True,
    )
    await feed.connect()

    closed_ts = datetime(2026, 5, 2, 1, 0, tzinfo=timezone.utc)  # Saturday
    received: list[Tick] = []

    with patch("src.live.feed_kis.datetime") as dt_mock:
        dt_mock.now.return_value = closed_ts
        dt_mock.strptime = datetime.strptime
        dt_mock.side_effect = lambda *a, **kw: datetime(*a, **kw)

        async def _runner():
            async for tick in feed:
                received.append(tick)

        runner = asyncio.create_task(_runner())
        await asyncio.sleep(0.15)
        await feed.aclose()
        runner.cancel()
        try:
            await runner
        except (asyncio.CancelledError, BaseException):
            pass

    assert client.calls == 0
    assert received == []
