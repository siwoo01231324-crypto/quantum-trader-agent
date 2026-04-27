"""Async parallel of sync OrderRouter — same responsibilities, async semantics.

Responsibilities mirrored from router.py:
  - kill-switch gate on place_order
  - metric emit (orders_total, orders_placed_total)
  - swap_active (env flag + cancel_all_open + snapshot)
  - health_check → trip kill switch on DOWN

New responsibility vs sync router:
  - optional KisRateLimiter integration (paper/live mode)

No new responsibilities beyond this list (Architect Principle 2).
"""
from __future__ import annotations

import os

from src.brokers.base import AsyncBrokerAdapter, HealthStatus, OrderAck, OrderRequest, Position
from src.brokers.kis.rate_limiter import KisRateLimiter
from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch, KillSwitchTripped


class AsyncOrderRouter:
    """Routes orders to the active async broker, enforcing kill switch and health gates."""

    def __init__(
        self,
        active: AsyncBrokerAdapter,
        kill_switch: KillSwitch | None = None,
        metrics: Metrics | None = None,
        rate_limiter: KisRateLimiter | None = None,
    ) -> None:
        self.active = active
        self._ks = kill_switch or KillSwitch()
        self._metrics = metrics
        self._rate_limiter = rate_limiter

    # ── order operations ──────────────────────────────────────────────────────

    async def place_order(self, req: OrderRequest) -> OrderAck:
        self._ks.assert_allow_order(liquidation=req.emergency_exit)
        if self._rate_limiter is not None:
            await self._rate_limiter.acquire()
        ack = await self.active.place_order(req)
        if self._metrics:
            self._metrics.orders_total.labels(
                strategy="unknown",
                broker=self.active.name,
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
        return snapshot

    async def aclose(self) -> None:
        await self.active.aclose()
