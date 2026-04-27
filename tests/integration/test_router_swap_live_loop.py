"""Stage 4.4: router_swap — run_shadow_loop broker_mode swap + WAL monotonic ts (Stage 4.4, #105)."""
from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.brokers.base import AsyncBrokerAdapter, HealthStatus, OrderAck
from src.live.feed import MarketDataFeed
from src.live.loop import ShadowConfig, _build_router, run_shadow_loop
from src.live.types import Tick
from src.live.wal import replay
from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch

from prometheus_client import CollectorRegistry


class FakeFeed(MarketDataFeed):
    """Emits N ticks then stops."""

    def __init__(self, ticks: list[Tick]) -> None:
        self._ticks = ticks
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def subscribe(self, symbols: list[str]) -> None:
        pass

    def __aiter__(self) -> AsyncIterator[Tick]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[Tick]:
        for t in self._ticks:
            yield t

    async def aclose(self) -> None:
        self._connected = False


def _make_ticks(n: int = 3) -> list[Tick]:
    return [
        Tick(
            symbol="005930",
            price=Decimal("70000"),
            qty=Decimal("1"),
            ts=datetime(2026, 4, 26, 9, 0, i, tzinfo=timezone.utc).isoformat(),
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 1. paper-only mode runs N ticks without error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shadow_loop_paper_only_mode():
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = ShadowConfig(
            symbols=["005930"],
            wal_path=Path(tmpdir) / "wal.jsonl",
            lock_path=Path(tmpdir) / ".lock",
            broker_mode="paper-only",
            max_iterations=3,
        )
        m = Metrics(registry=CollectorRegistry())
        ks = KillSwitch()
        feed = FakeFeed(_make_ticks(3))

        await run_shadow_loop(cfg, feed=feed, metrics=m, kill_switch=ks)

        assert not ks.tripped, "Kill switch should not trip in paper-only mode"


# ---------------------------------------------------------------------------
# 2. _build_router returns PaperBroker for paper-only
# ---------------------------------------------------------------------------

def test_build_router_paper_only_returns_paper_broker():
    from src.execution.paper_broker import PaperBroker
    from src.execution.mock_matching import MockMatchingEngine
    from src.live.wal import WAL

    with tempfile.TemporaryDirectory() as tmpdir:
        wal = WAL(Path(tmpdir) / "wal.jsonl")
        ks = KillSwitch()
        m = Metrics(registry=CollectorRegistry())
        paper = PaperBroker(wal=wal, kill_switch=ks)

        router = _build_router("paper-only", ks, m, paper)
        assert router is paper


# ---------------------------------------------------------------------------
# 3. _build_router returns AsyncOrderRouter for kis-paper
# ---------------------------------------------------------------------------

def test_build_router_kis_paper_returns_async_router():
    from src.brokers.async_router import AsyncOrderRouter
    from src.execution.paper_broker import PaperBroker
    from src.live.wal import WAL

    with tempfile.TemporaryDirectory() as tmpdir:
        wal = WAL(Path(tmpdir) / "wal.jsonl")
        ks = KillSwitch()
        m = Metrics(registry=CollectorRegistry())
        paper = PaperBroker(wal=wal, kill_switch=ks)

        kis_adapter = MagicMock(spec=AsyncBrokerAdapter)
        kis_adapter.name = "kis_paper"
        kis_adapter.paper = True

        router = _build_router("kis-paper", ks, m, paper, kis_adapter=kis_adapter)
        assert isinstance(router, AsyncOrderRouter)
        assert router.active is kis_adapter


# ---------------------------------------------------------------------------
# 4. _build_router raises without kis_adapter for kis-paper mode
# ---------------------------------------------------------------------------

def test_build_router_kis_paper_requires_adapter():
    from src.execution.paper_broker import PaperBroker
    from src.live.wal import WAL

    with tempfile.TemporaryDirectory() as tmpdir:
        wal = WAL(Path(tmpdir) / "wal.jsonl")
        ks = KillSwitch()
        m = Metrics(registry=CollectorRegistry())
        paper = PaperBroker(wal=wal, kill_switch=ks)

        with pytest.raises(ValueError, match="kis_adapter"):
            _build_router("kis-paper", ks, m, paper, kis_adapter=None)


# ---------------------------------------------------------------------------
# 5. WAL ts monotonic after shadow loop run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wal_events_ts_monotonic_after_loop():
    """All WAL events written during shadow loop have monotonically non-decreasing ts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = Path(tmpdir) / "wal.jsonl"
        cfg = ShadowConfig(
            symbols=["005930"],
            wal_path=wal_path,
            lock_path=Path(tmpdir) / ".lock",
            broker_mode="paper-only",
            max_iterations=2,
        )
        m = Metrics(registry=CollectorRegistry())
        ks = KillSwitch()
        feed = FakeFeed(_make_ticks(2))

        await run_shadow_loop(cfg, feed=feed, metrics=m, kill_switch=ks)

        events, _ = replay(wal_path)
        if len(events) >= 2:
            timestamps = [ev.ts for ev in events]
            for i in range(1, len(timestamps)):
                assert timestamps[i] >= timestamps[i - 1], (
                    f"WAL ts not monotonic: {timestamps[i-1]} > {timestamps[i]}"
                )
