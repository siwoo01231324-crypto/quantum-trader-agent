"""Async multi-strategy orchestrator — issue #78.

Not callable from LLM tool surface (CLAUDE.md invariant #6).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import random
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import pandas as pd

from risk import (
    Policy,
    Snapshot,
    Order,
    Action,
    evaluate,
    PortfolioRiskReport,
)
from src.brokers.base import AsyncBrokerAdapter
from src.live.types import EVENT_STRATEGY_TOGGLED, WALEvent
from .orchestrator import _SyncStrategyOrchestrator
from .order_intent import OrderIntent
from .sizing import resolve_size

logger = logging.getLogger(__name__)


class AsyncStrategyOrchestrator:
    """Async multi-strategy tick driver with quarantine, risk gating, and bar/wall-clock refresh.

    Composes _SyncStrategyOrchestrator for sync portfolio-risk operations.
    Sync API delegates lock-free; async API uses asyncio.Lock for write protection.
    """

    def __init__(
        self,
        policy: Policy,
        *,
        refresh_every_n_bars: int | None = None,
        min_reliability: float = 0.0,
        broker: AsyncBrokerAdapter | None = None,
        wal_observer: Callable[[WALEvent], None] | None = None,
    ) -> None:
        self._sync = _SyncStrategyOrchestrator(policy)
        self._policy = policy
        self._broker: AsyncBrokerAdapter | None = broker
        self._strategies: dict[str, object] = {}
        self._recent_returns: dict[str, pd.Series] = {}
        self._quarantined: set[str] = set()
        self._disabled: set[str] = set()
        self._fail_count: dict[str, int] = {}
        self._report_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None
        self._bar_count = 0
        self._refresh_every_n_bars = refresh_every_n_bars
        self._wal_observer = wal_observer

    # ---- sync delegation API -----------------------------------------------

    def register_strategy(self, strategy_id: str, strategy: object) -> None:
        self._strategies[strategy_id] = strategy
        self._fail_count.setdefault(strategy_id, 0)

    def register_strategy_returns(self, strategy_id: str, series: pd.Series) -> None:
        self._sync.register_strategy_returns(strategy_id, series)
        self._recent_returns[strategy_id] = series

    def refresh_portfolio_risk(self, ts=None) -> Optional[PortfolioRiskReport]:
        return self._sync.refresh_portfolio_risk(ts)

    def strategy_reliability_score(self, strategy_id: str) -> float:
        return self._sync.strategy_reliability_score(strategy_id)

    @property
    def quarantined_strategies(self) -> frozenset[str]:
        return frozenset(self._quarantined)

    @property
    def current_report(self) -> Optional[PortfolioRiskReport]:
        return self._sync.current_report

    # ---- enable / disable (#180) -------------------------------------------

    @property
    def disabled_strategies(self) -> frozenset[str]:
        return frozenset(self._disabled)

    def is_enabled(self, strategy_id: str) -> bool:
        return strategy_id not in self._disabled

    def enable_strategy(self, strategy_id: str) -> None:
        """Re-enable a previously disabled strategy.

        No-op if already enabled (audit event NOT emitted on no-op).
        """
        if strategy_id not in self._strategies:
            raise ValueError(f"strategy {strategy_id!r} not registered")
        if strategy_id not in self._disabled:
            return
        self._disabled.discard(strategy_id)
        self._emit_strategy_toggled(strategy_id, enabled=True)

    def disable_strategy(
        self,
        strategy_id: str,
        *,
        positions: list[tuple[str, float]] | None = None,
    ) -> list[OrderIntent]:
        """Disable a strategy: block new signals + emit WAL audit + return liquidation intents.

        Args:
            strategy_id: registered strategy id.
            positions: list of (symbol, qty) currently held by this strategy.
                Each non-zero qty produces a market-sell OrderIntent for the caller
                to submit (D1: 즉시 청산 — issue #180 user decision 2026-05-05).

        Returns:
            list[OrderIntent]: liquidation intents (side='sell') for each non-zero
            position. Empty if positions is None or all qtys are zero.

        Raises:
            ValueError: if strategy_id is not registered.

        Idempotent: disabling an already-disabled strategy still returns liquidation
        intents (caller may pass updated positions) but does NOT re-emit the WAL audit.
        """
        if strategy_id not in self._strategies:
            raise ValueError(f"strategy {strategy_id!r} not registered")

        was_enabled = strategy_id not in self._disabled
        self._disabled.add(strategy_id)
        if was_enabled:
            self._emit_strategy_toggled(strategy_id, enabled=False)

        intents: list[OrderIntent] = []
        if positions:
            for symbol, qty in positions:
                if qty <= 0:
                    continue
                intents.append(OrderIntent(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    side="sell",
                    qty=qty,
                    reason="strategy_disabled_liquidation",
                ))
        return intents

    def _emit_strategy_toggled(self, strategy_id: str, *, enabled: bool) -> None:
        if self._wal_observer is None:
            return
        ev = WALEvent(
            ts=datetime.now(timezone.utc).isoformat(),
            event_type=EVENT_STRATEGY_TOGGLED,
            payload={
                "strategy_id": strategy_id,
                "enabled": enabled,
                "actor": "user",
            },
        )
        try:
            self._wal_observer(ev)
        except Exception as err:
            logger.warning(
                "portfolio.orchestrator.wal_observer_error event=strategy_toggled "
                "strategy_id=%s enabled=%s error=%s",
                strategy_id, enabled, err,
            )

    # ---- async API ---------------------------------------------------------

    async def run_bar(
        self,
        ts,
        market_snapshot: dict,
        *,
        strategies: list[str] | None = None,
    ) -> list[OrderIntent]:
        targets = [
            sid for sid in self._strategies
            if sid not in self._quarantined
            and sid not in self._disabled
            and (strategies is None or sid in strategies)
        ]

        # Surface `factors` at ctx top-level (#177) so AsyncStrategies that read
        # `ctx["factors"][...]` (e.g. MomoKisV1 → ctx["factors"]["rsi"]) see
        # the precomputed series populated by SnapshotBuilder. Falls back to
        # empty dict for callers that don't supply factors.
        _factors = (market_snapshot or {}).get("factors", {}) if isinstance(market_snapshot, dict) else {}
        tasks = []
        sids = []
        for sid in targets:
            strategy = self._strategies[sid]
            ctx = {"ts": ts, "market_snapshot": market_snapshot, "factors": _factors}
            if inspect.iscoroutinefunction(strategy.on_bar):
                task = asyncio.create_task(strategy.on_bar(ctx))
            else:
                task = asyncio.create_task(asyncio.to_thread(strategy.on_bar, ctx))
            tasks.append(task)
            sids.append(sid)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        order_intents: list[OrderIntent] = []
        report_snapshot = self._sync.current_report

        for sid, result in zip(sids, results):
            if isinstance(result, BaseException):
                self._fail_count[sid] = self._fail_count.get(sid, 0) + 1
                count = self._fail_count[sid]
                logger.warning(
                    "portfolio.orchestrator.strategy_exception strategy_id=%s exception=%s",
                    sid,
                    result,
                )
                if count >= 3:
                    self._quarantined.add(sid)
                    logger.warning(
                        "portfolio.orchestrator.quarantine strategy_id=%s fail_count=%d",
                        sid,
                        count,
                    )
                continue

            signal = result
            if signal is None:
                continue

            self._fail_count[sid] = 0

            if signal.action == "hold":
                continue

            recent = self._recent_returns.get(sid)
            qty = resolve_size(signal, recent)

            order = Order(
                symbol=market_snapshot.get("symbol", "UNKNOWN"),
                side=signal.action,
                qty=qty,
                price=market_snapshot.get("price", 0.0),
            )
            snap = Snapshot(
                intent=order,
                equity_krw=market_snapshot.get("equity_krw", 0.0),
                portfolio_risk=report_snapshot,
            )
            decision = evaluate(self._policy, snap)

            if decision.action == Action.ALLOW:
                order_intents.append(OrderIntent(
                    strategy_id=sid,
                    symbol=order.symbol,
                    side=signal.action,
                    qty=qty,
                    reason=signal.reason,
                ))
            else:
                logger.info(
                    "risk.breach rule_id=%s",
                    decision.rule_id,
                )

        self._bar_count += 1
        if (
            self._refresh_every_n_bars is not None
            and self._bar_count % self._refresh_every_n_bars == 0
        ):
            await self.refresh_portfolio_risk_async(ts)

        return order_intents

    async def refresh_portfolio_risk_async(self, ts=None) -> Optional[PortfolioRiskReport]:
        t0 = time.monotonic()
        async with self._report_lock:
            report = self._sync.refresh_portfolio_risk(ts)
        age_sec = time.monotonic() - t0
        n = len(self._strategies)
        logger.info(
            "portfolio.orchestrator.risk_refresh age_sec=%.1f n_strategies=%d",
            age_sec,
            n,
        )
        return report

    async def start_risk_refresh_loop(
        self,
        interval_sec: float = 300.0,
        jitter_frac: float = 0.1,
    ) -> None:
        async def _loop() -> None:
            while True:
                jitter = interval_sec * jitter_frac * (2 * random.random() - 1)
                await asyncio.sleep(max(0.0, interval_sec + jitter))
                await self.refresh_portfolio_risk_async()

        self._refresh_task = asyncio.create_task(_loop())

    async def stop_risk_refresh_loop(self) -> None:
        if self._refresh_task is not None:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None
