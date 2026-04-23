from __future__ import annotations

import json
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from src.observability import METRIC_NAMES, Metrics


@pytest.fixture
def metrics() -> Metrics:
    return Metrics(registry=CollectorRegistry())


def test_minimum_ten_metric_names_defined():
    assert len(METRIC_NAMES) >= 10
    assert len(set(METRIC_NAMES)) == len(METRIC_NAMES)


def test_all_metrics_emit_to_registry(metrics: Metrics):
    metrics.orders_total.labels("momo-v1", "kis", "BUY", "submitted").inc()
    metrics.fills_total.labels("momo-v1", "kis", "BUY").inc()
    metrics.fill_qty_total.labels("momo-v1", "kis", "BUY").inc(100)
    metrics.pnl_current.labels("momo-v1").set(123456.0)
    metrics.position_qty.labels("momo-v1", "005930").set(50)
    metrics.order_latency_seconds.labels("kis", "twap").observe(0.12)
    metrics.market_data_lag_seconds.labels("krx-feed").set(0.05)
    metrics.kill_switch_state.labels("manual").set(0)
    metrics.strategy_signal_total.labels("momo-v1", "long").inc()
    metrics.risk_breach_total.labels("max_drawdown", "warn").inc()

    payload = generate_latest(metrics.registry).decode("utf-8")
    for name in METRIC_NAMES:
        assert name in payload, f"{name} not exposed"


def test_naming_convention(metrics: Metrics):
    for name in METRIC_NAMES:
        assert name.startswith("qta_"), f"{name} must start with qta_"


def test_counter_monotonic(metrics: Metrics):
    c = metrics.orders_total.labels("momo-v1", "kis", "BUY", "submitted")
    c.inc()
    c.inc(2)
    payload = generate_latest(metrics.registry).decode("utf-8")
    assert "qta_orders_total" in payload


def test_grafana_dashboards_have_min_panels():
    dash_dir = Path(__file__).resolve().parents[2] / "grafana" / "dashboards"
    for name in ("system.json", "strategy.json", "execution.json"):
        data = json.loads((dash_dir / name).read_text(encoding="utf-8"))
        assert len(data["panels"]) >= 4, f"{name} has fewer than 4 panels"
        assert data["uid"], f"{name} missing uid"


def test_loki_labels_doc_present():
    doc = Path(__file__).resolve().parents[2] / "loki" / "labels.md"
    text = doc.read_text(encoding="utf-8")
    for required in ("trace_id", "severity", "strategy", "broker"):
        assert required in text
