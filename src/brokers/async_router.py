"""Async parallel of sync OrderRouter — same responsibilities, async semantics.

Responsibilities mirrored from router.py:
  - kill-switch gate on place_order
  - metric emit (orders_total, orders_placed_total)
  - swap_active (env flag + cancel_all_open + snapshot)
  - health_check → trip kill switch on DOWN
  - cost-based dynamic routing via ExecutionCostEstimator

New responsibility vs sync router:
  - optional KisRateLimiter integration (paper/live mode)

No new responsibilities beyond this list (Architect Principle 2).
"""
from __future__ import annotations

import os

from src.brokers.base import AsyncBrokerAdapter, HealthStatus, OrderAck, OrderRequest, Position
from src.brokers.kis.rate_limiter import KisRateLimiter
from src.brokers.router import ExecutionCostEstimator
from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch, KillSwitchTripped


class AsyncOrderRouter:
    """Routes orders to the active async broker, enforcing kill switch and health gates.

    When multiple brokers are registered via `register_broker()`, place_order()
    automatically selects the lowest-cost broker based on ExecutionCostEstimator scores.
    """

    def __init__(
        self,
        active: AsyncBrokerAdapter,
        kill_switch: KillSwitch | None = None,
        metrics: Metrics | None = None,
        rate_limiter: KisRateLimiter | None = None,
        cost_estimator: ExecutionCostEstimator | None = None,
    ) -> None:
        self.active = active
        self._ks = kill_switch or KillSwitch()
        self._metrics = metrics
        self._rate_limiter = rate_limiter
        self._cost_estimator = cost_estimator or ExecutionCostEstimator()
        self._brokers: dict[str, AsyncBrokerAdapter] = {active.name: active}

    @property
    def name(self) -> str:
        # #238 — executor.py 가 broker.name 으로 metrics 라벨링하는데 router 가
        # adapter 아니라 wrapper 라 직접 name 속성 없었음 (AttributeError).
        # active 브로커 name 으로 위임.
        return self.active.name

    @property
    def paper(self) -> bool:
        # executor.py 의 tracking_sample 가드에서도 broker.paper 접근.
        return getattr(self.active, "paper", False)

    # ── broker registry ───────────────────────────────────────────────────────

    def register_broker(self, broker: AsyncBrokerAdapter) -> None:
        """Register an additional broker candidate for cost-based routing."""
        self._brokers[broker.name] = broker

    def _select_broker(self, force_broker: str | None) -> AsyncBrokerAdapter:
        if force_broker is not None:
            if force_broker not in self._brokers:
                raise KeyError(f"force_broker '{force_broker}' not registered")
            return self._brokers[force_broker]
        if len(self._brokers) <= 1:
            return self.active
        best_name = self._cost_estimator.best_broker(list(self._brokers.keys()))
        return self._brokers[best_name]

    # ── order operations ──────────────────────────────────────────────────────

    async def place_order(self, req: OrderRequest, *, force_broker: str | None = None) -> OrderAck:
        self._ks.assert_allow_order(liquidation=req.emergency_exit)
        if self._rate_limiter is not None:
            await self._rate_limiter.acquire()
        broker = self._select_broker(force_broker)
        ack = await broker.place_order(req)
        if self._metrics:
            self._metrics.orders_total.labels(
                strategy="unknown",
                broker=broker.name,
                side=req.side.value,
                status=ack.status,
            ).inc()
            self._metrics.orders_placed_total.labels(
                strategy="unknown",
                status=ack.status,
            ).inc()
        return ack

    async def cancel_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> None:
        await self.active.cancel_order(
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            symbol=symbol,
        )

    async def get_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> OrderAck:
        return await self.active.get_order(
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            symbol=symbol,
        )

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        return await self.active.get_positions(symbol)

    async def get_balance(self):
        return await self.active.get_balance()

    # ── ensure_leverage_minimum forward (2026-06-03 PR #353) ─────────────────
    # executor 가 발주 직전 broker.ensure_leverage_minimum(symbol) 을
    # getattr 로 호출. 본 router 가 active adapter 의 같은 메서드로 forward
    # 안 하면 getattr 가 None → -1109 Invalid account 거부 폭주
    # (logs/live/20260602T182844Z: 786 sell signals 전량 거부).
    # active adapter 가 미지원이면 graceful skip.
    async def ensure_leverage_minimum(
        self, symbol: str, fallback_leverage: int = 1,
    ) -> None:
        m = getattr(self.active, "ensure_leverage_minimum", None)
        if m is None:
            return
        await m(symbol, fallback_leverage)

    # ── ensure_leverage_target forward (#380) ────────────────────────────────
    # executor 가 QTA_TARGET_LEVERAGE 설정 시 발주 직전 호출 — leverage 를
    # config 값으로 *강제* (minimum 과 달리 현재값 override). active adapter
    # 미지원(KIS/Paper)이면 graceful skip.
    async def ensure_leverage_target(self, symbol: str, leverage: int) -> None:
        m = getattr(self.active, "ensure_leverage_target", None)
        if m is None:
            return
        await m(symbol, leverage)

    # ── health ────────────────────────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        status = await self.active.health_check()
        if status == HealthStatus.DOWN:
            self._ks.trip(reason="broker_unhealthy", source="auto:health_check")
            if self._metrics:
                self._metrics.risk_breach_total.labels(
                    rule="broker_unhealthy", severity="critical"
                ).inc()
        return status

    # ── broker swap ───────────────────────────────────────────────────────────

    async def swap_active(self, new_broker: AsyncBrokerAdapter) -> list[Position]:
        """Safely swap the active broker.

        Protocol:
        1. Check BROKER_ROUTER_ENABLED feature flag.
        2. Snapshot current positions.
        3. Cancel all open orders on old broker (best-effort, hasattr-guarded).
        4. Switch to new broker.
        Returns position snapshot from old broker.
        """
        if os.environ.get("BROKER_ROUTER_ENABLED", "false").lower() != "true":
            raise RuntimeError(
                "BROKER_ROUTER_ENABLED is not set to 'true'. "
                "Enable the feature flag before swapping brokers."
            )

        snapshot = await self.active.get_positions()
        if hasattr(self.active, "cancel_all_open"):
            await self.active.cancel_all_open()  # type: ignore[attr-defined]

        self.active = new_broker
        self._brokers[new_broker.name] = new_broker
        return snapshot

    async def aclose(self) -> None:
        await self.active.aclose()
