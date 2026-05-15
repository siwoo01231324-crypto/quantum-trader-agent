"""S3 (#231) — _select_feed routing for the new `kis-ws` feed_mode."""
from __future__ import annotations

import pytest

from src.live.feed_kis_ws import KISWebSocketMarketFeed
from src.live.loop import ShadowConfig, _select_feed


class _StubAuth:
    """Stand-in for KISAuth; KISWebSocketMarketFeed only stores it."""


class _StubKISClient:
    """Minimal KISClient surface required by _select_feed (kis-ws branch)."""
    def __init__(self) -> None:
        self._auth = _StubAuth()
        self._app_key = "stub_app_key"


def test_kis_ws_returns_ws_feed():
    """feed_mode='kis-ws' + kis_client → KISWebSocketMarketFeed instance."""
    config = ShadowConfig(
        symbols=["005930", "035720"],
        feed_mode="kis-ws",
        kis_client=_StubKISClient(),
    )
    feed = _select_feed(config)
    assert isinstance(feed, KISWebSocketMarketFeed)


def test_kis_ws_without_client_raises():
    """feed_mode='kis-ws' but kis_client=None → ValueError."""
    config = ShadowConfig(
        symbols=["005930"],
        feed_mode="kis-ws",
        kis_client=None,
    )
    with pytest.raises(ValueError, match="kis-ws"):
        _select_feed(config)


def test_kis_rest_still_routed_to_rest_feed():
    """Regression — feed_mode='kis' (REST polling) keeps using KISMarketFeed."""
    from src.live.feed_kis import KISMarketFeed
    config = ShadowConfig(
        symbols=["005930"],
        feed_mode="kis",
        kis_client=_StubKISClient(),
    )
    feed = _select_feed(config)
    assert isinstance(feed, KISMarketFeed)
    assert not isinstance(feed, KISWebSocketMarketFeed)


def test_auto_mode_unaffected_by_kis_ws_addition():
    """Regression — feed_mode='auto' + KRX symbol → KISMarketFeed (not WS)."""
    from src.live.feed_kis import KISMarketFeed
    config = ShadowConfig(
        symbols=["005930"],
        feed_mode="auto",
        kis_client=_StubKISClient(),
    )
    feed = _select_feed(config)
    assert isinstance(feed, KISMarketFeed)
