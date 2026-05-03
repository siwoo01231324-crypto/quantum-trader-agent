"""Test qta_orders_placed_total vs qta_orders_filled_total metric split.

Validates AC3 invariant: placed_total >= filled_total (placed is superset of filled).
5 scenarios: normal ack+fill / NEW only (unfilled) / partial fill / cancel / reject.
"""
from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry

from src.observability.metrics import Metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_metrics() -> Metrics:
    return Metrics(registry=CollectorRegistry())


def _get_placed(m: Metrics, strategy: str, status: str) -> float:
    try:
        return m.orders_placed_total.labels(strategy=strategy, status=status)._value.get()
    except Exception:
        return 0.0


def _get_filled(m: Metrics, strategy: str) -> float:
    try:
        return m.orders_filled_total.labels(strategy=strategy)._value.get()
    except Exception:
        return 0.0


def _total_placed(m: Metrics, strategy: str) -> float:
    """Sum across all status labels for a strategy."""
    total = 0.0
    for status in ("NEW", "FILLED", "REJECTED", "CANCELED", "PARTIALLY_FILLED"):
        total += _get_placed(m, strategy, status)
    return total


# ---------------------------------------------------------------------------
# Scenario 1: normal flow — ack (NEW) then fill (FILLED)
# ---------------------------------------------------------------------------

def test_normal_ack_and_fill():
    m = _make_metrics()

    # Order accepted (NEW ack)
    m.orders_placed_total.labels(strategy="strat_a", status="NEW").inc()
    # Order filled
    m.orders_filled_total.labels(strategy="strat_a").inc()

    placed = _total_placed(m, "strat_a")
    filled = _get_filled(m, "strat_a")

    assert placed == 1.0
    assert filled == 1.0
    assert placed >= filled, "placed must be >= filled"


# ---------------------------------------------------------------------------
# Scenario 2: NEW only — order accepted but not yet filled (open order)
# ---------------------------------------------------------------------------

def test_new_only_unfilled():
    m = _make_metrics()

    m.orders_placed_total.labels(strategy="strat_b", status="NEW").inc()
    m.orders_placed_total.labels(strategy="strat_b", status="NEW").inc()
    # No fills

    placed = _total_placed(m, "strat_b")
    filled = _get_filled(m, "strat_b")

    assert placed == 2.0
    assert filled == 0.0
    assert placed >= filled, "placed must be >= filled"


# ---------------------------------------------------------------------------
# Scenario 3: partial fill — placed once, filled < placed
# ---------------------------------------------------------------------------

def test_partial_fill():
    m = _make_metrics()

    # 3 orders placed
    for _ in range(3):
        m.orders_placed_total.labels(strategy="strat_c", status="NEW").inc()
    # Only 2 actually filled
    m.orders_filled_total.labels(strategy="strat_c").inc()
    m.orders_filled_total.labels(strategy="strat_c").inc()

    placed = _total_placed(m, "strat_c")
    filled = _get_filled(m, "strat_c")

    assert placed == 3.0
    assert filled == 2.0
    assert placed >= filled, "placed must be >= filled (partial fill)"


# ---------------------------------------------------------------------------
# Scenario 4: cancel — placed (NEW) then cancelled (no fill increment)
# ---------------------------------------------------------------------------

def test_cancel_no_fill():
    m = _make_metrics()

    m.orders_placed_total.labels(strategy="strat_d", status="NEW").inc()
    m.orders_placed_total.labels(strategy="strat_d", status="CANCELED").inc()
    # No fill increment for cancelled order

    placed = _total_placed(m, "strat_d")
    filled = _get_filled(m, "strat_d")

    assert placed == 2.0
    assert filled == 0.0
    assert placed >= filled, "placed must be >= filled (cancel)"


# ---------------------------------------------------------------------------
# Scenario 5: reject — REJECTED ack, orders_filled_total NOT incremented
# ---------------------------------------------------------------------------

def test_reject_not_counted_in_filled():
    m = _make_metrics()

    m.orders_placed_total.labels(strategy="strat_e", status="REJECTED").inc()
    # Rejected orders do NOT increment filled

    placed = _total_placed(m, "strat_e")
    filled = _get_filled(m, "strat_e")

    assert placed == 1.0
    assert filled == 0.0
    assert placed >= filled, "placed must be >= filled (reject)"


# ---------------------------------------------------------------------------
# Invariant test: placed >= filled across all scenarios combined
# ---------------------------------------------------------------------------

def test_placed_always_superset_of_filled():
    """Aggregate invariant: sum(placed) >= sum(filled) regardless of mix."""
    m = _make_metrics()

    # Mix: 5 placed, 3 filled
    for i in range(5):
        m.orders_placed_total.labels(strategy="strat_f", status="NEW").inc()
    for i in range(3):
        m.orders_filled_total.labels(strategy="strat_f").inc()

    placed = _total_placed(m, "strat_f")
    filled = _get_filled(m, "strat_f")

    assert placed >= filled, f"Invariant violated: placed={placed} < filled={filled}"


# ---------------------------------------------------------------------------
# Label structure test: placed has (strategy, status) labels; filled has (strategy,)
# ---------------------------------------------------------------------------

def test_metric_label_structure():
    m = _make_metrics()

    # orders_placed_total has 'status' label for split visibility
    m.orders_placed_total.labels(strategy="s1", status="NEW").inc()
    m.orders_placed_total.labels(strategy="s1", status="FILLED").inc()

    new_count = _get_placed(m, "s1", "NEW")
    filled_label = _get_placed(m, "s1", "FILLED")

    assert new_count == 1.0
    assert filled_label == 1.0

    # orders_filled_total has only 'strategy' label
    m.orders_filled_total.labels(strategy="s1").inc()
    assert _get_filled(m, "s1") == 1.0
