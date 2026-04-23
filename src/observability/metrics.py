"""Prometheus metrics catalog for quantum-trader-agent.

10 core metrics. Spec: docs/specs/observability.md.
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

METRIC_NAMES = [
    "qta_orders_total",
    "qta_fills_total",
    "qta_fill_qty_total",
    "qta_pnl_current",
    "qta_position_qty",
    "qta_order_latency_seconds",
    "qta_market_data_lag_seconds",
    "qta_kill_switch_state",
    "qta_strategy_signal_total",
    "qta_risk_breach_total",
    "qta_open_orders",
]


class Metrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self.orders_total = Counter(
            "qta_orders_total",
            "Total orders submitted",
            labelnames=("strategy", "broker", "side", "status"),
            registry=self.registry,
        )
        self.fills_total = Counter(
            "qta_fills_total",
            "Total fills received",
            labelnames=("strategy", "broker", "side"),
            registry=self.registry,
        )
        self.fill_qty_total = Counter(
            "qta_fill_qty_total",
            "Total filled quantity",
            labelnames=("strategy", "broker", "side"),
            registry=self.registry,
        )
        self.pnl_current = Gauge(
            "qta_pnl_current",
            "Current PnL in KRW (realized + unrealized)",
            labelnames=("strategy",),
            registry=self.registry,
        )
        self.position_qty = Gauge(
            "qta_position_qty",
            "Current position quantity",
            labelnames=("strategy", "symbol"),
            registry=self.registry,
        )
        self.order_latency_seconds = Histogram(
            "qta_order_latency_seconds",
            "Order submit-to-ack latency",
            labelnames=("broker", "algo"),
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
            registry=self.registry,
        )
        self.market_data_lag_seconds = Gauge(
            "qta_market_data_lag_seconds",
            "Market data ingestion lag",
            labelnames=("source",),
            registry=self.registry,
        )
        self.kill_switch_state = Gauge(
            "qta_kill_switch_state",
            "Kill switch state (1=triggered, 0=normal)",
            labelnames=("reason",),
            registry=self.registry,
        )
        self.strategy_signal_total = Counter(
            "qta_strategy_signal_total",
            "Strategy signal events",
            labelnames=("strategy", "signal"),
            registry=self.registry,
        )
        self.risk_breach_total = Counter(
            "qta_risk_breach_total",
            "Risk rule breaches",
            labelnames=("rule", "severity"),
            registry=self.registry,
        )
        self.open_orders = Gauge(
            "qta_open_orders",
            "Current open order count",
            labelnames=("broker", "symbol"),
            registry=self.registry,
        )


_default: Metrics | None = None


def get_registry() -> Metrics:
    global _default
    if _default is None:
        _default = Metrics()
    return _default
