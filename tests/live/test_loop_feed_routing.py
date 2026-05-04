"""Tests for ShadowConfig feed routing in src.live.loop (#177)."""
from __future__ import annotations

import pytest

from src.live.feed import BinancePublicFeed
from src.live.feed_kis import KISMarketFeed, MockReplayFeed
from src.live.loop import ShadowConfig, _select_feed


def test_select_feed_auto_with_krx_symbol_returns_kis():
    cfg = ShadowConfig(symbols=["005930"], feed_mode="auto", kis_client=object())
    feed = _select_feed(cfg)
    assert isinstance(feed, KISMarketFeed)


def test_select_feed_auto_with_btc_symbol_returns_binance():
    cfg = ShadowConfig(symbols=["BTCUSDT"], feed_mode="auto")
    feed = _select_feed(cfg)
    assert isinstance(feed, BinancePublicFeed)


def test_select_feed_explicit_binance_overrides_krx_auto():
    cfg = ShadowConfig(symbols=["005930"], feed_mode="binance")
    feed = _select_feed(cfg)
    assert isinstance(feed, BinancePublicFeed)


def test_select_feed_kis_without_client_raises():
    cfg = ShadowConfig(symbols=["005930"], feed_mode="kis")
    with pytest.raises(ValueError, match="kis_client"):
        _select_feed(cfg)


def test_select_feed_mock_returns_replay():
    cfg = ShadowConfig(symbols=["005930"], feed_mode="mock", mock_ticks=[])
    feed = _select_feed(cfg)
    assert isinstance(feed, MockReplayFeed)
