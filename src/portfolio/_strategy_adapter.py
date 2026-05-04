"""Adapter: sync on_bar(bar, history, context) → async-orchestrator on_bar(ctx).

AsyncStrategyOrchestrator.run_bar passes a single ``ctx = {"ts", "market_snapshot"}``
dict. Sync strategies like MomoBtcV2 use the legacy Protocol signature
``on_bar(bar, history, context)``. This adapter pulls those fields from the
populated ``market_snapshot`` (built by `src.live.snapshot_builder.SnapshotBuilder`):

  - ``bar``     ← derived from ``snapshot["history"]`` last row
  - ``history`` ← ``snapshot["history"]``
  - ``context`` ← ``{"factors": snapshot["factors"], "ts": ctx["ts"]}``

Bug-fix history (#177): the adapter previously read ``ctx["bar"]`` /
``ctx["history"]`` / ``ctx["context"]`` directly, but the orchestrator never
supplies those keys at the top level — production strategies always observed
``None`` and short-circuited. The fix lands here rather than on the
orchestrator to avoid disturbing async strategies that rely on the existing
``ctx["market_snapshot"]`` shape.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from backtest.protocol import Bar, Signal


def _bar_from_history(history: pd.DataFrame | None) -> Bar | None:
    if history is None or len(history) == 0:
        return None
    last = history.iloc[-1]
    return Bar(
        ts=history.index[-1],
        open=float(last["open"]),
        high=float(last["high"]),
        low=float(last["low"]),
        close=float(last["close"]),
        volume=float(last.get("volume", 0.0)),
    )


class _StrategyAdapter:
    """Wraps a sync Strategy so AsyncStrategyOrchestrator can call on_bar(ctx)."""

    def __init__(self, strategy: object) -> None:
        self._strategy = strategy

    def on_bar(self, ctx: dict[str, Any]) -> Signal:
        snap: dict[str, Any] = ctx.get("market_snapshot", {}) or {}
        history = snap.get("history")
        bar = _bar_from_history(history)
        context: dict[str, Any] = {
            "ts": ctx.get("ts"),
            "factors": snap.get("factors", {}),
            # Surface the snapshot's symbol so sync strategies can self-gate
            # (#177 cross-asset leak fix — see backtest/strategies/momo_btc_v2).
            "symbol": snap.get("symbol"),
        }
        return self._strategy.on_bar(bar, history, context)  # type: ignore[return-value]
