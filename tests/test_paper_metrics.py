"""Unit tests for Phase 1 paper-specific metrics (#80).

Verifies that the 8 new metrics are correctly registered in the Metrics class
and that their types, labels, and operations work as expected.
"""
from __future__ import annotations

import pytest
from prometheus_client import Counter, Gauge, Histogram

from src.observability.metrics import METRIC_NAMES, Metrics


@pytest.fixture()
def m() -> Metrics:
    """Fresh Metrics instance with isolated registry per test."""
    return Metrics()


def test_paper_fills_total_registered(m: Metrics) -> None:
    assert hasattr(m, "paper_fills_total")
    assert isinstance(m.paper_fills_total, Counter)


def test_paper_pnl_usdt_registered(m: Metrics) -> None:
    assert hasattr(m, "paper_pnl_usdt")
    assert isinstance(m.paper_pnl_usdt, Gauge)


def test_paper_equity_usdt_no_label(m: Metrics) -> None:
    # Should not raise — no labelnames on this gauge
    m.paper_equity_usdt.set(50000)
    samples = list(m.paper_equity_usdt.collect())
    values = [s.value for metric in samples for s in metric.samples if s.name == "qta_paper_equity_usdt"]
    assert any(v == 50000.0 for v in values)


def test_paper_order_ack_latency_ms_observe(m: Metrics) -> None:
    m.paper_order_ack_latency_ms.observe(50)
    samples = list(m.paper_order_ack_latency_ms.collect())
    # _count sample should be 1 after one observe()
    counts = [s.value for metric in samples for s in metric.samples if s.name == "qta_paper_order_ack_latency_ms_count"]
    assert any(v == 1.0 for v in counts)


def test_wal_write_error_total_with_label(m: Metrics) -> None:
    m.wal_write_error_total.labels(error_type="OSError").inc()
    samples = list(m.wal_write_error_total.collect())
    values = [
        s.value
        for metric in samples
        for s in metric.samples
        if s.name == "qta_wal_write_error_total_total" or s.name == "qta_wal_write_error_total"
    ]
    assert any(v == 1.0 for v in values)


def test_metric_names_includes_8_new() -> None:
    expected = {
        "qta_paper_fills_total",
        "qta_paper_pnl_usdt",
        "qta_paper_position_qty",
        "qta_paper_equity_usdt",
        "qta_paper_order_ack_latency_ms",
        "qta_paper_drawdown_ratio",
        "qta_paper_fee_usdt_total",
        "qta_wal_write_error_total",
    }
    assert expected.issubset(set(METRIC_NAMES))


def test_paper_fills_total_labels_strategy_symbol_side(m: Metrics) -> None:
    m.paper_fills_total.labels(strategy="X", symbol="BTCUSDT", side="BUY").inc()
    samples = list(m.paper_fills_total.collect())
    values = [
        s.value
        for metric in samples
        for s in metric.samples
        if (s.name == "qta_paper_fills_total_total" or s.name == "qta_paper_fills_total")
        and s.labels.get("strategy") == "X"
        and s.labels.get("symbol") == "BTCUSDT"
        and s.labels.get("side") == "BUY"
    ]
    assert any(v == 1.0 for v in values)


def test_paper_drawdown_ratio_can_set_negative(m: Metrics) -> None:
    m.paper_drawdown_ratio.set(-0.05)
    samples = list(m.paper_drawdown_ratio.collect())
    values = [s.value for metric in samples for s in metric.samples if s.name == "qta_paper_drawdown_ratio"]
    assert any(abs(v - (-0.05)) < 1e-9 for v in values)
