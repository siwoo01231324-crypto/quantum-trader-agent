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
from src.live.types import EVENT_STRATEGY_EVALUATED, EVENT_STRATEGY_TOGGLED, WALEvent
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
        # #238 — live-scanner 포지션 중복 진입 차단. ATR breakout 등 조건이
        # 지속되는 동안 매 tick(초당 수 건) 재매수하던 폭주 버그 fix. (sid,
        # symbol) 진입 시 기록, LivePositionRiskManager 가 청산하면
        # release_live_position() 으로 해제 → 재진입 허용.
        self._live_entered: set[tuple[str, str]] = set()
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

    def release_live_position(self, strategy_id: str, symbol: str) -> None:
        """#238 — LivePositionRiskManager 가 stop/TP 청산 시 호출. (sid, symbol)
        진입 기록을 해제해 다음 조건 충족 시 재진입 허용. 미호출 시 해당
        strategy-symbol 은 프로세스 수명 동안 1회만 진입 (안전 측 fail-safe)."""
        self._live_entered.discard((strategy_id, symbol))

    def refresh_portfolio_risk(self, ts=None) -> Optional[PortfolioRiskReport]:
        return self._sync.refresh_portfolio_risk(ts)

    def strategy_reliability_score(self, strategy_id: str) -> float:
        return self._sync.strategy_reliability_score(strategy_id)

    @property
    def quarantined_strategies(self) -> frozenset[str]:
        return frozenset(self._quarantined)

    @property
    def strategies(self) -> dict[str, object]:
        """Read-only snapshot of registered strategies (#227 S3).

        Used by external wiring (e.g. live_run.py) to discover registered
        strategies and read their class attributes — typically the
        ``LiveScannerMixin`` exit thresholds.
        """
        return dict(self._strategies)

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

    def _emit_strategy_evaluated(
        self,
        strategy_id: str,
        *,
        symbol: str,
        decision: str,
        reason: str,
        ts: object,
    ) -> None:
        """Emit `strategy_evaluated` WAL event (#231 S5).

        Called once per (strategy, symbol) pair in run_bar dispatch — gives
        runtime visibility into on_bar invocation regardless of buy/sell/hold
        outcome. Used by AC0_strategy_dispatch + AC5 (24h dispatch ≥ 1000).
        Decision values: "buy" | "sell" | "hold" | "exception".
        """
        if self._wal_observer is None:
            return
        ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        ev = WALEvent(
            ts=ts_str,
            event_type=EVENT_STRATEGY_EVALUATED,
            payload={
                "strategy_id": strategy_id,
                "symbol": symbol,
                "decision": decision,
                "reason": reason,
            },
        )
        try:
            self._wal_observer(ev)
        except Exception as err:
            logger.warning(
                "portfolio.orchestrator.wal_observer_error event=strategy_evaluated "
                "strategy_id=%s symbol=%s decision=%s error=%s",
                strategy_id, symbol, decision, err,
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
        _snap_dict = market_snapshot if isinstance(market_snapshot, dict) else {}
        _factors = _snap_dict.get("factors", {})
        # Live-scanner per-symbol dispatch inputs (#227 S1). Both keys optional —
        # absence keeps every strategy on the legacy single-dispatch path.
        _universe_ohlcv = _snap_dict.get("ohlcv_history")
        _universe_factors = _snap_dict.get("universe_factors", {}) or {}
        _equity_krw = _snap_dict.get("equity_krw", 0.0)

        tasks = []
        sids = []
        # Per-task symbol override — set for live-scanner per-symbol dispatch,
        # None for legacy strategies (cs_*, momo_*, single-ticker).
        task_symbols: list[str | None] = []

        def _spawn(strategy, ctx):
            if inspect.iscoroutinefunction(strategy.on_bar):
                return asyncio.create_task(strategy.on_bar(ctx))
            return asyncio.create_task(asyncio.to_thread(strategy.on_bar, ctx))

        for sid in targets:
            strategy = self._strategies[sid]
            if (
                getattr(strategy, "is_live_scanner", False)
                and isinstance(_universe_ohlcv, dict)
                and _universe_ohlcv
            ):
                # #227 S1 — iterate the universe and create one task per symbol.
                # Each task receives a single-symbol market_snapshot + the
                # per-symbol factors slice (or empty dict if none registered).
                for symbol, hist in _universe_ohlcv.items():
                    if hist is None or len(hist) == 0:
                        continue
                    last_close = float(hist["close"].iloc[-1])
                    per_symbol_snap = {
                        "symbol": symbol,
                        "history": hist,
                        "price": last_close,
                        "equity_krw": _equity_krw,
                    }
                    per_symbol_factors = _universe_factors.get(symbol, {}) or {}
                    ctx = {
                        "ts": ts,
                        "market_snapshot": per_symbol_snap,
                        "factors": per_symbol_factors,
                    }
                    tasks.append(_spawn(strategy, ctx))
                    sids.append(sid)
                    task_symbols.append(symbol)
            else:
                ctx = {"ts": ts, "market_snapshot": market_snapshot, "factors": _factors}
                tasks.append(_spawn(strategy, ctx))
                sids.append(sid)
                task_symbols.append(None)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        order_intents: list[OrderIntent] = []
        report_snapshot = self._sync.current_report
        # Dedup quarantine accounting to once per (sid, run_bar call) — a
        # live-scanner that throws across N symbols in one tick must not
        # increment the failure counter N times (#227 S1).
        counted_failed: set[str] = set()

        for sid, sym_override, result in zip(sids, task_symbols, results):
            # Resolve symbol once — used by S5 WAL event AND order routing.
            order_symbol = sym_override or _snap_dict.get("symbol", "UNKNOWN")

            if isinstance(result, BaseException):
                if sid not in counted_failed:
                    counted_failed.add(sid)
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
                self._emit_strategy_evaluated(
                    sid, symbol=order_symbol, decision="exception",
                    reason=type(result).__name__, ts=ts,
                )
                continue

            signal = result
            if signal is None:
                self._emit_strategy_evaluated(
                    sid, symbol=order_symbol, decision="hold",
                    reason="no_signal", ts=ts,
                )
                continue

            # Reset only when no sibling task for this sid threw in the same
            # tick — preserves the spirit of "consecutive bad ticks" semantics
            # for live-scanner strategies.
            if sid not in counted_failed:
                self._fail_count[sid] = 0

            if signal.action == "hold":
                self._emit_strategy_evaluated(
                    sid, symbol=order_symbol, decision="hold",
                    reason="action_hold", ts=ts,
                )
                continue

            # #238 — live-scanner 포지션 중복 진입 차단. 조건(ATR breakout 등)이
            # 지속되면 매 tick buy 신호가 나오는데, 이미 (sid, symbol) 포지션을
            # 보유 중이면 추가 매수하지 않는다 (1 position per strategy-symbol).
            # 청산은 LivePositionRiskManager 가 stop/TP 로 수행 →
            # release_live_position() 호출 시 재진입 가능.
            is_live = getattr(self._strategies.get(sid), "is_live_scanner", False)
            if is_live and signal.action == "buy":
                key = (sid, order_symbol)
                if key in self._live_entered:
                    self._emit_strategy_evaluated(
                        sid, symbol=order_symbol, decision="hold",
                        reason="live_position_open", ts=ts,
                    )
                    continue
                self._live_entered.add(key)

            # buy/sell — emit before order routing so the event captures
            # strategy intent regardless of downstream risk-gate decision.
            self._emit_strategy_evaluated(
                sid, symbol=order_symbol, decision=signal.action,
                reason=getattr(signal, "reason", None) or "entry", ts=ts,
            )

            recent = self._recent_returns.get(sid)
            qty = resolve_size(signal, recent)
            order_price = (
                _snap_dict.get("price", 0.0)
                if sym_override is None
                else float(_universe_ohlcv[sym_override]["close"].iloc[-1])
            )
            order = Order(
                symbol=order_symbol,
                side=signal.action,
                qty=qty,
                price=order_price,
            )
            snap = Snapshot(
                intent=order,
                equity_krw=_equity_krw,
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
