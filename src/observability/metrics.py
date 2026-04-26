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
    "qta_broker_fill_queue_overflow_total",
    "qta_broker_ws_reconnect_total",
    "qta_broker_keepalive_failure_total",
    "qta_broker_request_latency_seconds",
    # Phase 1 paper-specific (#80)
    "qta_paper_fills_total",
    "qta_paper_pnl_usdt",
    "qta_paper_position_qty",
    "qta_paper_equity_usdt",
    "qta_paper_order_ack_latency_ms",
    "qta_paper_drawdown_ratio",
    "qta_paper_fee_usdt_total",
    "qta_wal_write_error_total",
    # Phase 2 KIS paper-specific (#105)
    "qta_paper_pnl_krw",
    "qta_paper_equity_krw",
    "qta_kis_token_ttl_seconds",
    "qta_kis_partial_fill_total",
    "qta_paper_kis_tracking_error",
    "qta_broker_rate_limit_hit_total",
    "qta_kis_fill_missing_total",
    # qta_orders_placed_total: NEW ack count (order accepted by exchange)
    # qta_orders_filled_total: FILLED confirmed count (execution complete)
    # NOTE: qta_orders_total (existing) covers all ack types (NEW/FILLED/CANCELED/etc.)
    "qta_orders_placed_total",
    "qta_orders_filled_total",
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
        self.broker_fill_queue_overflow_total = Counter(
            "qta_broker_fill_queue_overflow_total",
            "Fill queue overflow events by broker and overflow policy",
            labelnames=("broker", "policy"),
            registry=self.registry,
        )
        self.broker_ws_reconnect_total = Counter(
            "qta_broker_ws_reconnect_total",
            "WebSocket reconnect attempts by broker and reason",
            labelnames=("broker", "reason"),
            registry=self.registry,
        )
        self.broker_keepalive_failure_total = Counter(
            "qta_broker_keepalive_failure_total",
            "listenKey keepalive failures by broker",
            labelnames=("broker",),
            registry=self.registry,
        )
        self.broker_request_latency_seconds = Histogram(
            "qta_broker_request_latency_seconds",
            "Broker REST request latency in seconds",
            labelnames=("broker", "method", "endpoint"),
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
            registry=self.registry,
        )
        # Phase 1 paper-specific (#80)
        self.paper_fills_total = Counter(
            "qta_paper_fills_total",
            "Paper broker fill events (#80)",
            labelnames=("strategy", "symbol", "side"),
            registry=self.registry,
        )
        self.paper_pnl_usdt = Gauge(
            "qta_paper_pnl_usdt",
            "Paper PnL in USDT (realized + unrealized) (#80)",
            labelnames=("strategy",),
            registry=self.registry,
        )
        self.paper_position_qty = Gauge(
            "qta_paper_position_qty",
            "Paper position quantity by symbol (#80)",
            labelnames=("strategy", "symbol"),
            registry=self.registry,
        )
        self.paper_equity_usdt = Gauge(
            "qta_paper_equity_usdt",
            "Paper total equity (cash + unrealized) in USDT (#80)",
            registry=self.registry,
        )
        self.paper_order_ack_latency_ms = Histogram(
            "qta_paper_order_ack_latency_ms",
            "Paper order submit-to-ack latency in milliseconds (#80)",
            buckets=(1, 5, 10, 25, 50, 100, 250, 500, 1000),
            registry=self.registry,
        )
        self.paper_drawdown_ratio = Gauge(
            "qta_paper_drawdown_ratio",
            "Paper current drawdown ratio (peak-relative, negative) (#80)",
            registry=self.registry,
        )
        self.paper_fee_usdt_total = Counter(
            "qta_paper_fee_usdt_total",
            "Paper cumulative fees paid in USDT (#80)",
            labelnames=("symbol", "fee_type"),
            registry=self.registry,
        )
        self.wal_write_error_total = Counter(
            "qta_wal_write_error_total",
            "WAL write failure count by error type (#80)",
            labelnames=("error_type",),
            registry=self.registry,
        )
        # Phase 2 KIS paper-specific (#105)
        self.paper_pnl_krw = Gauge(
            "qta_paper_pnl_krw",
            "Paper PnL in KRW (realized + unrealized) (#105)",
            labelnames=("strategy",),
            registry=self.registry,
        )
        self.paper_equity_krw = Gauge(
            "qta_paper_equity_krw",
            "Paper total equity (cash + unrealized) in KRW (#105)",
            labelnames=("strategy",),
            registry=self.registry,
        )
        self.kis_token_ttl_seconds = Gauge(
            "qta_kis_token_ttl_seconds",
            "KIS OAuth token remaining TTL in seconds (#105)",
            labelnames=("paper",),
            registry=self.registry,
        )
        self.kis_partial_fill_total = Counter(
            "qta_kis_partial_fill_total",
            "KIS partial fill events (#105)",
            labelnames=("strategy",),
            registry=self.registry,
        )
        self.paper_kis_tracking_error = Gauge(
            "qta_paper_kis_tracking_error",
            "Paper vs KIS live tracking error (basis points) (#105)",
            labelnames=("strategy",),
            registry=self.registry,
        )
        self.broker_rate_limit_hit_total = Counter(
            "qta_broker_rate_limit_hit_total",
            "Broker rate-limit hits by broker and scope (#105)",
            labelnames=("broker", "scope"),
            registry=self.registry,
        )
        self.kis_fill_missing_total = Counter(
            "qta_kis_fill_missing_total",
            "KIS fill events with missing order mapping (#105)",
            labelnames=("strategy",),
            registry=self.registry,
        )
        # orders_placed_total: NEW ack count (order accepted by exchange)
        # orders_filled_total: FILLED confirmed count (execution complete)
        # orders_total (existing): all ack types (NEW/FILLED/CANCELED/etc.)
        self.orders_placed_total = Counter(
            "qta_orders_placed_total",
            "Orders accepted by exchange (NEW ack) — AC3 1차 (#105)",
            labelnames=("strategy", "status"),
            registry=self.registry,
        )
        self.orders_filled_total = Counter(
            "qta_orders_filled_total",
            "Orders fully filled (FILLED confirmed) — AC3 최종 (#105)",
            labelnames=("strategy",),
            registry=self.registry,
        )


_default: Metrics | None = None


def get_registry() -> Metrics:
    global _default
    if _default is None:
        _default = Metrics()
    return _default
