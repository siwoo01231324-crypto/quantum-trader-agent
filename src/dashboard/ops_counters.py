"""Operational diagnostics counters for the dashboard (#231 follow-up).

Folds WAL events into a small struct exposed via /api/ops + a dashboard card.
Mirrors the diagnostics surfaced by `daily_check_kis.ps1` so operators don't
have to drop to PowerShell to answer "did anything actually happen after I
pressed 거래 시작?".

The counters are populated by the same `_wal_observer` that feeds the timeline
broker / pnl aggregator / position store — so wiring this through is one line
in `live_run.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class OpsCounters:
    """Lifetime + per-decision counters fed from WAL events."""

    # Lifetime counts of significant WAL event types.
    bars_seen: int = 0                  # bar_received | tick events (best-effort)
    strategy_evaluated: int = 0         # on_bar dispatch count (#231 S5)
    signal_emitted: int = 0
    order_submitted: int = 0
    order_filled: int = 0
    errors: int = 0                     # error_logged or exception decisions

    # strategy_evaluated breakdown by decision.
    decisions: dict[str, int] = field(default_factory=lambda: {
        "buy": 0, "sell": 0, "hold": 0, "exception": 0,
    })

    # Last-seen timestamps per event type (ISO strings, populated from payload.ts).
    last_bar_ts: str | None = None
    last_signal_ts: str | None = None
    last_order_ts: str | None = None
    last_fill_ts: str | None = None
    last_error_ts: str | None = None

    # Most recent fill summary — pre-formatted for the dashboard card.
    last_fill_detail: str | None = None

    # Per-strategy on_bar count — helps spot starved strategies.
    by_strategy: dict[str, int] = field(default_factory=dict)

    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def ingest(self, event_type: str, payload: dict[str, Any] | None) -> None:
        """Single entry point — called from the WAL fan-out observer."""
        payload = payload or {}
        ts = str(payload.get("ts") or payload.get("server_ts") or "")
        with self._lock:
            if event_type in ("bar_received", "tick_received"):
                self.bars_seen += 1
                if ts:
                    self.last_bar_ts = ts
            elif event_type == "strategy_evaluated":
                self.strategy_evaluated += 1
                decision = str(payload.get("decision") or "hold")
                self.decisions[decision] = self.decisions.get(decision, 0) + 1
                sid = str(payload.get("strategy_id") or "")
                if sid:
                    self.by_strategy[sid] = self.by_strategy.get(sid, 0) + 1
            elif event_type == "signal_emitted":
                self.signal_emitted += 1
                if ts:
                    self.last_signal_ts = ts
            elif event_type == "order_submitted" or event_type == "order_placed":
                self.order_submitted += 1
                if ts:
                    self.last_order_ts = ts
            elif event_type in ("order_filled", "fill_received"):
                self.order_filled += 1
                if ts:
                    self.last_fill_ts = ts
                sym = payload.get("symbol", "?")
                side = payload.get("side", "?")
                qty = payload.get("qty") or payload.get("quantity") or ""
                price = payload.get("price") or payload.get("fill_price") or ""
                self.last_fill_detail = f"{side} {qty} {sym} @ {price}".strip()
            elif event_type in ("error_logged", "exception"):
                self.errors += 1
                if ts:
                    self.last_error_ts = ts

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe dict for /api/ops."""
        with self._lock:
            return {
                "bars_seen": self.bars_seen,
                "strategy_evaluated": self.strategy_evaluated,
                "signal_emitted": self.signal_emitted,
                "order_submitted": self.order_submitted,
                "order_filled": self.order_filled,
                "errors": self.errors,
                "decisions": dict(self.decisions),
                "last_bar_ts": self.last_bar_ts,
                "last_signal_ts": self.last_signal_ts,
                "last_order_ts": self.last_order_ts,
                "last_fill_ts": self.last_fill_ts,
                "last_error_ts": self.last_error_ts,
                "last_fill_detail": self.last_fill_detail,
                "by_strategy": dict(self.by_strategy),
            }
