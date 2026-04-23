from __future__ import annotations

import os
from typing import Callable

from src.brokers.base import (
    BrokerAdapter, OrderRequest, OrderAck, Position, Balance,
    HealthStatus, MarginType, Closeable,
)
from src.brokers.errors import BrokerStartupError
from src.brokers.types import BrokerFill
from src.ops.kill_switch import KillSwitch, KillSwitchTripped


class OrderRouter:
    """Routes orders to the active broker, enforcing kill switch and health gates."""

    def __init__(
        self,
        active: BrokerAdapter,
        kill_switch: KillSwitch | None = None,
        metrics=None,
    ) -> None:
        self.active = active
        self._ks = kill_switch or KillSwitch()
        self._metrics = metrics

    # ── order operations ──────────────────────────────────────────────────────

    def place_order(self, req: OrderRequest) -> OrderAck:
        self._ks.assert_allow_order(liquidation=req.emergency_exit)
        ack = self.active.place_order(req)
        if self._metrics:
            self._metrics.orders_total.labels(
                strategy="unknown",
                broker=self.active.name,
                side=req.side.value,
                status=ack.status,
            ).inc()
        return ack

    def cancel_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> None:
        self.active.cancel_order(
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            symbol=symbol,
        )

    def get_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> OrderAck:
        return self.active.get_order(
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            symbol=symbol,
        )

    def get_positions(self, symbol: str | None = None) -> list[Position]:
        return self.active.get_positions(symbol)

    def get_balance(self) -> list[Balance]:
        return self.active.get_balance()

    def stream_fills(self, on_fill: Callable[[BrokerFill], None]) -> Closeable:
        return self.active.stream_fills(on_fill)

    # ── health ────────────────────────────────────────────────────────────────

    def health_check(self) -> HealthStatus:
        status = self.active.health_check()
        if status == HealthStatus.DOWN:
            self._ks.trip(reason="broker_unhealthy", source="auto:health_check")
            if self._metrics:
                self._metrics.risk_breach_total.labels(
                    rule="broker_unhealthy", severity="critical"
                ).inc()
        return status

    # ── broker swap ───────────────────────────────────────────────────────────

    def swap_active(self, new_broker: BrokerAdapter) -> list[Position]:
        """Safely swap the active broker.

        Protocol:
        1. Check BROKER_ROUTER_ENABLED feature flag.
        2. Snapshot current positions.
        3. Cancel all open orders on old broker (best-effort).
        4. Switch to new broker.
        Returns position snapshot from old broker.
        """
        if os.environ.get("BROKER_ROUTER_ENABLED", "false").lower() != "true":
            raise RuntimeError(
                "BROKER_ROUTER_ENABLED is not set to 'true'. "
                "Enable the feature flag before swapping brokers."
            )

        snapshot = self.active.get_positions()
        # best-effort cancel all — adapters implement cancel_all or we no-op
        if hasattr(self.active, "cancel_all_open"):
            self.active.cancel_all_open()  # type: ignore[attr-defined]

        self.active = new_broker
        return snapshot
