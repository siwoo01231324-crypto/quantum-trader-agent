from __future__ import annotations

import os
from collections import defaultdict, deque
from decimal import Decimal
from typing import Callable

from src.brokers.base import (
    BrokerAdapter, OrderRequest, OrderAck, Position, Balance,
    HealthStatus, MarginType, Closeable,
)
from src.brokers.errors import BrokerStartupError
from src.brokers.is_estimator import MarketSnapshot, pre_flight_is_estimate
from src.brokers.types import BrokerFill
from src.ops.kill_switch import KillSwitch, KillSwitchTripped


class ExecutionCostEstimator:
    """Estimates per-broker execution cost from recent fills.

    Score = mean(slippage_ratio) + mean(fee_ratio), where:
      slippage_ratio = (fill.price - mid_price) / mid_price  (signed; positive = paid)
      fee_ratio      = fill.fee / (fill.qty * fill.price)

    Lower score → cheaper broker.
    """

    def __init__(self, window: int = 50) -> None:
        self._window = window
        # broker_name -> deque of (slippage_ratio, fee_ratio) tuples
        self._samples: dict[str, deque[tuple[Decimal, Decimal]]] = defaultdict(
            lambda: deque(maxlen=self._window)
        )

    def record_fill(self, broker_name: str, fill: BrokerFill, mid_price: Decimal) -> None:
        if mid_price <= 0:
            return
        notional = fill.qty * fill.price
        if notional <= 0:
            return
        slippage = (fill.price - mid_price) / mid_price
        fee_ratio = fill.fee / notional
        self._samples[broker_name].append((slippage, fee_ratio))

    def cost_score(self, broker_name: str) -> Decimal:
        samples = self._samples.get(broker_name)
        if not samples:
            return Decimal("0")
        n = Decimal(len(samples))
        total_slip = sum(s for s, _ in samples)
        total_fee = sum(f for _, f in samples)
        return (total_slip + total_fee) / n

    def best_broker(self, broker_names: list[str]) -> str:
        return min(broker_names, key=lambda n: self.cost_score(n))


class OrderRouter:
    """Routes orders to the active broker, enforcing kill switch and health gates.

    When multiple brokers are registered via `register_broker()`, place_order()
    automatically selects the lowest-cost broker based on ExecutionCostEstimator scores.
    Strategies may override routing with `req.algo_params["force_broker"]` (if present
    on the request; ignored for plain OrderRequest which has no algo_params field).
    """

    def __init__(
        self,
        active: BrokerAdapter,
        kill_switch: KillSwitch | None = None,
        metrics=None,
        cost_estimator: ExecutionCostEstimator | None = None,
    ) -> None:
        self.active = active
        self._ks = kill_switch or KillSwitch()
        self._metrics = metrics
        self._cost_estimator = cost_estimator or ExecutionCostEstimator()
        # registry: name -> broker (active is always included)
        self._brokers: dict[str, BrokerAdapter] = {active.name: active}

    # ── broker registry ───────────────────────────────────────────────────────

    def register_broker(self, broker: BrokerAdapter) -> None:
        """Register an additional broker candidate for cost-based routing."""
        self._brokers[broker.name] = broker

    def _select_broker(self, force_broker: str | None) -> BrokerAdapter:
        if force_broker is not None:
            if force_broker not in self._brokers:
                raise KeyError(f"force_broker '{force_broker}' not registered")
            return self._brokers[force_broker]
        if len(self._brokers) <= 1:
            return self.active
        best_name = self._cost_estimator.best_broker(list(self._brokers.keys()))
        return self._brokers[best_name]

    # ── order operations ──────────────────────────────────────────────────────

    def place_order(
        self,
        req: OrderRequest,
        *,
        force_broker: str | None = None,
        market_snap: MarketSnapshot | None = None,
    ) -> OrderAck:
        self._ks.assert_allow_order(liquidation=req.emergency_exit)
        broker = self._select_broker(force_broker)
        if self._metrics and market_snap is not None:
            is_est = pre_flight_is_estimate(
                symbol=req.symbol,
                side=req.side,
                qty=float(req.qty),
                snap=market_snap,
            )
            self._metrics.is_estimate_bps.labels(
                broker=broker.name,
                symbol=req.symbol,
            ).observe(is_est)
        ack = broker.place_order(req)
        if self._metrics:
            self._metrics.orders_total.labels(
                strategy="unknown",
                broker=broker.name,
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
        self._brokers[new_broker.name] = new_broker
        return snapshot
