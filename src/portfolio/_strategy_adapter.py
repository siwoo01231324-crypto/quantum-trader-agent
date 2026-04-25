"""Adapter: sync on_bar(bar, history, context) → async-orchestrator on_bar(ctx).

AsyncStrategyOrchestrator.run_bar passes a single `ctx` dict to on_bar.
Strategies like MomoBtcV2 use the legacy Protocol signature
on_bar(bar, history, context).  This adapter bridges the two.
"""
from __future__ import annotations

from backtest.protocol import Bar, Signal


class _StrategyAdapter:
    """Wraps a sync Strategy so AsyncStrategyOrchestrator can call on_bar(ctx)."""

    def __init__(self, strategy: object) -> None:
        self._strategy = strategy

    def on_bar(self, ctx: dict) -> Signal:
        bar: Bar = ctx.get("bar")  # type: ignore[assignment]
        history = ctx.get("history")
        context = ctx.get("context", {})
        return self._strategy.on_bar(bar, history, context)  # type: ignore[return-value]
