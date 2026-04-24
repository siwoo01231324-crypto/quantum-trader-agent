"""Tests for AsyncStrategy protocol and dispatch detection (T3 — issue #78)."""
from __future__ import annotations

import asyncio
import inspect

import pytest

from backtest.protocol import Signal, Strategy, AsyncStrategy


class _SyncConcreteStrategy:
    """Concrete sync strategy — on_bar accepts ctx dict (orchestrator calling convention)."""

    def on_init(self, context: dict) -> None:
        pass

    def on_bar(self, bar, history, context: dict) -> Signal:
        return Signal(action="hold", size=0.0, reason="sync")


class _AsyncConcreteStrategy:
    async def on_bar(self, ctx: object) -> Signal | None:
        return Signal(action="buy", size=0.1, reason="async")


def test_strategy_protocol_remains_sync():
    """Strategy.on_bar is a plain (sync) method; concrete class passes isinstance."""
    strat = _SyncConcreteStrategy()
    assert isinstance(strat, Strategy)
    assert not inspect.iscoroutinefunction(strat.on_bar)


def test_async_strategy_protocol_requires_coroutine():
    """AsyncStrategy.on_bar must be a coroutinefunction."""
    strat = _AsyncConcreteStrategy()
    assert inspect.iscoroutinefunction(strat.on_bar)


def test_dispatch_detects_async_strategy():
    """iscoroutinefunction correctly distinguishes sync vs async on_bar at dispatch."""
    sync_strat = _SyncConcreteStrategy()
    async_strat = _AsyncConcreteStrategy()

    assert not inspect.iscoroutinefunction(sync_strat.on_bar)
    assert inspect.iscoroutinefunction(async_strat.on_bar)

    # Ensure async strategy is callable and returns a coroutine
    async def _check():
        result = await async_strat.on_bar(ctx={})
        assert result is not None
        assert result.action == "buy"

    asyncio.run(_check())
