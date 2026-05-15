"""S3 (#231) — _select_feed routing for the new `kis-ws` feed_mode."""
from __future__ import annotations

import pytest

from src.live.feed_kis_ws import (
    KISWebSocketMarketFeed,
    MultiConnectionKISWebSocketFeed,
)
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


# ── #231 S3-part2: multi-connection for >40 symbols ─────────────────────────


def test_kis_ws_40_symbols_uses_single_connection():
    """Exactly 40 symbols → single KISWebSocketMarketFeed (no multi-conn)."""
    config = ShadowConfig(
        symbols=[f"{i:06d}" for i in range(40)],
        feed_mode="kis-ws",
        kis_client=_StubKISClient(),
    )
    feed = _select_feed(config)
    assert isinstance(feed, KISWebSocketMarketFeed)
    assert not isinstance(feed, MultiConnectionKISWebSocketFeed)


def test_kis_ws_200_symbols_uses_multi_connection():
    """200 symbols → MultiConnectionKISWebSocketFeed with 5 connections."""
    config = ShadowConfig(
        symbols=[f"{i:06d}" for i in range(200)],
        feed_mode="kis-ws",
        kis_client=_StubKISClient(),
    )
    feed = _select_feed(config)
    assert isinstance(feed, MultiConnectionKISWebSocketFeed)
    assert feed.n_connections == 5


def test_kis_ws_41_symbols_uses_multi_connection():
    """Boundary: 41 symbols → 2-connection split (40 + 1)."""
    config = ShadowConfig(
        symbols=[f"{i:06d}" for i in range(41)],
        feed_mode="kis-ws",
        kis_client=_StubKISClient(),
    )
    feed = _select_feed(config)
    assert isinstance(feed, MultiConnectionKISWebSocketFeed)
    assert feed.n_connections == 2


def test_multi_conn_shares_auth_and_app_key():
    """All sub-connections reuse the same KISAuth + app_key (single token)."""
    config = ShadowConfig(
        symbols=[f"{i:06d}" for i in range(80)],
        feed_mode="kis-ws",
        kis_client=_StubKISClient(),
    )
    feed = _select_feed(config)
    assert isinstance(feed, MultiConnectionKISWebSocketFeed)
    auths = {id(f._auth) for f in feed.feeds}
    app_keys = {f._app_key for f in feed.feeds}
    assert len(auths) == 1
    assert len(app_keys) == 1
