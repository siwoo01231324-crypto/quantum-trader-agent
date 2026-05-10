"""Stagger / max_qpm options for KISMarketFeed (#227 S5).

Verifies the rate-limit-friendly polling modes added so a 350-symbol KRX
universe fits inside the KIS REST budget (paper: 60 req/min) instead of
bursting all N calls at the start of every minute.

Strategy: rather than monkey-patching ``asyncio.sleep`` (which deadlocks
pytest-asyncio), the tests use small poll intervals and observe wall-clock
fetch ordering directly.
"""
from __future__ import annotations

import asyncio
import time
from collections import namedtuple

import pytest

from src.live.feed_kis import KISMarketFeed


_RawBar = namedtuple("_RawBar", "date time open high low close volume")


@pytest.fixture
def patch_fetch(monkeypatch):
    """Replace fetch_intraday_ohlcv_raw with a recorder."""
    calls: list[tuple[str, float]] = []  # (symbol, monotonic_ts)

    def _fake_fetch(client, symbol, date, *, interval):
        calls.append((symbol, time.monotonic()))
        # Return a fresh bar each call so a tick is yielded.
        return [_RawBar(
            date,
            time.strftime("%H%M%S"),  # unique time per call so tick yields
            100, 101, 99, 100, 1000,
        )]

    monkeypatch.setattr(
        "src.brokers.kis.price_client.fetch_intraday_ohlcv_raw", _fake_fetch,
    )
    return calls


async def _drain(feed: KISMarketFeed, n_ticks: int, timeout: float = 5.0):
    """Read up to *n_ticks* from the feed and close it."""
    out = []
    deadline = time.monotonic() + timeout
    async for tick in feed:
        out.append(tick)
        if len(out) >= n_ticks or time.monotonic() > deadline:
            await feed.aclose()
            break
    return out


class TestStagger:
    @pytest.mark.asyncio
    async def test_stagger_spreads_calls_evenly(self, patch_fetch):
        # poll_interval=0.4s, 4 symbols → stagger 0.1s between fetches.
        feed = KISMarketFeed(
            ["A", "B", "C", "D"], object(),
            poll_interval_sec=0.4,
            stagger=True,
            market_open_check=False,
        )
        ticks = await _drain(feed, n_ticks=4, timeout=2.0)
        assert len(ticks) == 4

        # Verify the fetch ordering matches the symbol order in the cycle, and
        # the time gap between consecutive calls is ≈ 0.1s (within tolerance).
        gaps = [
            patch_fetch[i + 1][1] - patch_fetch[i][1]
            for i in range(len(patch_fetch) - 1)
        ]
        # First-cycle gaps (3 of them between 4 fetches) should be ≈ 0.1s.
        first_cycle = gaps[:3]
        assert all(0.05 <= g <= 0.25 for g in first_cycle), (
            f"stagger gaps off: {first_cycle}"
        )

    @pytest.mark.asyncio
    async def test_no_stagger_keeps_legacy_burst(self, patch_fetch):
        # Without stagger, all symbols are fetched back-to-back at start of cycle.
        feed = KISMarketFeed(
            ["A", "B"], object(),
            poll_interval_sec=0.3,
            stagger=False,
            market_open_check=False,
        )
        ticks = await _drain(feed, n_ticks=2, timeout=2.0)
        assert len(ticks) == 2
        # Two fetches at start of cycle → gap < 0.05s.
        if len(patch_fetch) >= 2:
            burst_gap = patch_fetch[1][1] - patch_fetch[0][1]
            assert burst_gap < 0.05, (
                f"non-stagger should burst — gap was {burst_gap:.3f}s"
            )

    @pytest.mark.asyncio
    async def test_max_qpm_rotates_over_cycles(self, patch_fetch):
        # 5 symbols, max_qpm=2 → first cycle [A,B], second [C,D], third [E,A]…
        feed = KISMarketFeed(
            ["A", "B", "C", "D", "E"], object(),
            poll_interval_sec=0.05,
            max_qpm=2,
            market_open_check=False,
        )
        ticks = await _drain(feed, n_ticks=6, timeout=2.0)
        called_symbols = {s for s, _ in patch_fetch}
        assert called_symbols == {"A", "B", "C", "D", "E"}, (
            f"max_qpm rotation should eventually cover all symbols; got {called_symbols}"
        )
