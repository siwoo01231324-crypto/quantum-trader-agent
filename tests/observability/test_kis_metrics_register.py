"""Test Stage 1.1: KRW Phase 2 metrics registration in Metrics and METRIC_NAMES."""
from prometheus_client import CollectorRegistry

from src.observability.metrics import METRIC_NAMES, Metrics

EXPECTED_NEW = [
    "qta_paper_pnl_krw",
    "qta_paper_equity_krw",
    "qta_kis_token_ttl_seconds",
    "qta_kis_partial_fill_total",
    "qta_paper_kis_tracking_error",
    "qta_broker_rate_limit_hit_total",
    "qta_kis_fill_missing_total",
    "qta_orders_placed_total",
    "qta_orders_filled_total",
]


def test_metric_names_contains_9_new_metrics():
    for name in EXPECTED_NEW:
        assert name in METRIC_NAMES, f"{name} missing from METRIC_NAMES"


def test_metrics_instance_has_9_new_attributes():
    m = Metrics(registry=CollectorRegistry())
    attr_map = {
        "qta_paper_pnl_krw": "paper_pnl_krw",
        "qta_paper_equity_krw": "paper_equity_krw",
        "qta_kis_token_ttl_seconds": "kis_token_ttl_seconds",
        "qta_kis_partial_fill_total": "kis_partial_fill_total",
        "qta_paper_kis_tracking_error": "paper_kis_tracking_error",
        "qta_broker_rate_limit_hit_total": "broker_rate_limit_hit_total",
        "qta_kis_fill_missing_total": "kis_fill_missing_total",
        "qta_orders_placed_total": "orders_placed_total",
        "qta_orders_filled_total": "orders_filled_total",
    }
    for metric_name, attr in attr_map.items():
        assert hasattr(m, attr), f"Metrics missing attribute '{attr}' for {metric_name}"


def test_existing_metric_names_preserved():
    existing = [
        "qta_orders_total",
        "qta_fills_total",
        "qta_paper_pnl_usdt",
        "qta_wal_write_error_total",
    ]
    for name in existing:
        assert name in METRIC_NAMES, f"Existing metric {name} was removed from METRIC_NAMES"


def test_orders_placed_vs_filled_vs_total_semantics():
    """orders_placed_total = NEW ack count; orders_filled_total = FILLED confirmed count.
    orders_total (existing) covers all ack types (NEW/FILLED/CANCELED/etc.)."""
    m = Metrics(registry=CollectorRegistry())
    assert hasattr(m, "orders_total"), "orders_total must remain"
    assert hasattr(m, "orders_placed_total"), "orders_placed_total (NEW ack) required"
    assert hasattr(m, "orders_filled_total"), "orders_filled_total (FILLED confirmed) required"
