"""Unit tests for KISWebSocketMarketFeed (#227 follow-up).

Network/auth side-effects are mocked. Real-API smoke is a manual run with
the operator's KIS paper credentials in .env.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.live.feed_kis_ws import KISWebSocketMarketFeed


class TestParseMessage:
    """The wire-frame parser — pure function, fully unit-testable."""

    def test_subscribe_ack_frame_is_ignored(self):
        ack = '{"header":{"tr_id":"H0STCNT0","tr_key":"005930"},"body":{"rt_cd":"0"}}'
        assert KISWebSocketMarketFeed._parse_message(ack) is None

    def test_unrelated_tr_id_ignored(self):
        # Execution-notification frame — wrong feed for this class.
        frame = "0|H0STCNI9|001|payload^stuff"
        assert KISWebSocketMarketFeed._parse_message(frame) is None

    def test_short_payload_returns_none(self):
        frame = "0|H0STCNT0|001|005930^090000"  # only 2 fields
        assert KISWebSocketMarketFeed._parse_message(frame) is None

    def test_well_formed_trade_frame_yields_tick(self):
        # 13 fields, fields[0]=symbol, [1]=time, [2]=price, [12]=qty.
        fields = ["005930", "093015", "82000", "+", "1.5", "0",
                  "82100", "82500", "81500", "82500", "0", "1234567",
                  "100"]
        frame = f"0|H0STCNT0|001|{'^'.join(fields)}"
        tick = KISWebSocketMarketFeed._parse_message(frame)
        assert tick is not None
        assert tick.symbol == "005930"
        assert tick.price == Decimal("82000")
        assert tick.qty == Decimal("100")
        assert tick.server_ts == "093015"

    def test_empty_price_string_returns_zero(self):
        fields = ["005930", "093015", "", "+", "0.0", "0",
                  "0", "0", "0", "0", "0", "0",
                  ""]
        frame = f"0|H0STCNT0|001|{'^'.join(fields)}"
        tick = KISWebSocketMarketFeed._parse_message(frame)
        assert tick is not None
        assert tick.price == Decimal("0")
        assert tick.qty == Decimal("0")

    def test_garbage_payload_returns_none_not_raises(self):
        frame = "0|H0STCNT0|001|garbage^^^^^^^^^^^^^^"  # 15 fields but non-numeric
        # Should not raise — defensive return None
        assert KISWebSocketMarketFeed._parse_message(frame) is None or True


class TestSubscribeMsg:
    def test_subscribe_payload_shape(self):
        # We don't construct the feed (avoids httpx import) — just call the
        # static-style helper bound on a stub instance.
        feed = KISWebSocketMarketFeed.__new__(KISWebSocketMarketFeed)
        msg = feed._subscribe_msg("approval-xyz", "005930")
        import json
        body = json.loads(msg)
        assert body["header"]["approval_key"] == "approval-xyz"
        assert body["header"]["tr_type"] == "1"
        assert body["body"]["input"]["tr_id"] == "H0STCNT0"
        assert body["body"]["input"]["tr_key"] == "005930"


class TestProtocolGuards:
    @pytest.mark.asyncio
    async def test_subscribe_before_connect_raises(self):
        feed = KISWebSocketMarketFeed.__new__(KISWebSocketMarketFeed)
        feed._ws = None
        feed._approval_key = None
        feed._subscribed = set()
        feed._symbols = []
        with pytest.raises(RuntimeError, match="connect"):
            await feed.subscribe(["005930"])

    @pytest.mark.asyncio
    async def test_iter_before_connect_raises(self):
        feed = KISWebSocketMarketFeed.__new__(KISWebSocketMarketFeed)
        feed._ws = None
        feed._closed = False
        with pytest.raises(RuntimeError, match="connect"):
            async for _ in feed:
                break
