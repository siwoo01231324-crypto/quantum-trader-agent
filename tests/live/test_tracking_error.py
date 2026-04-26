"""Test Stage 5.1: tracking_error.py — formula, aggregation, gauge emit."""
from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry

from src.live.tracking_error import (
    TrackingErrorReport,
    TrackingSample,
    aggregate_from_wal,
    compute_tracking_error,
    daily_report_row,
    emit_gauge,
)
from src.live.types import WALEvent, EVENT_TRACKING_SAMPLE, TrackingSamplePayload
from src.live.wal import WAL
from src.observability.metrics import Metrics


def _make_sample(kis_price: str, sim_price: str, broker_order_id: str = "bo1") -> TrackingSample:
    return TrackingSample(
        broker_order_id=broker_order_id,
        kis_fill_price=Decimal(kis_price),
        sim_fill_price=Decimal(sim_price),
    )


# ---------------------------------------------------------------------------
# Formula tests
# ---------------------------------------------------------------------------

def test_compute_tracking_error_mean_formula():
    """mean(|kis - sim| / sim) for known values."""
    samples = [
        _make_sample("100.0", "100.0", "b1"),   # 0.00
        _make_sample("101.0", "100.0", "b2"),   # 0.01
        _make_sample("99.5",  "100.0", "b3"),   # 0.005
        _make_sample("102.0", "100.0", "b4"),   # 0.02
        _make_sample("98.0",  "100.0", "b5"),   # 0.02
    ]
    report = compute_tracking_error(samples)
    assert report.n_samples == 5
    assert report.n_missing == 0
    expected_mean = (0.0 + 0.01 + 0.005 + 0.02 + 0.02) / 5
    assert abs(float(report.mean_tracking_error) - expected_mean) < 1e-9


def test_compute_tracking_error_p50_p95():
    samples = [_make_sample(str(100 + i), "100.0", f"b{i}") for i in range(10)]
    report = compute_tracking_error(samples)
    assert report.p50 is not None
    assert report.p95 is not None
    assert report.p95 >= report.p50


def test_compute_tracking_error_empty():
    report = compute_tracking_error([])
    assert report.n_samples == 0
    assert report.mean_tracking_error == Decimal("0")


def test_compute_tracking_error_below_threshold():
    samples = [_make_sample("100.1", "100.0", f"b{i}") for i in range(5)]
    report = compute_tracking_error(samples)
    assert not report.exceeds_threshold


def test_compute_tracking_error_exceeds_threshold():
    # 1% error per sample → mean = 0.01 > 0.005 threshold
    samples = [_make_sample("101.0", "100.0", f"b{i}") for i in range(5)]
    report = compute_tracking_error(samples)
    assert report.exceeds_threshold


# ---------------------------------------------------------------------------
# WAL aggregation
# ---------------------------------------------------------------------------

def _write_wal_with_samples(path: Path, samples: list[tuple[str, str]]) -> None:
    wal = WAL(path)
    for i, (kis, sim) in enumerate(samples):
        payload = asdict(TrackingSamplePayload(
            client_order_id=f"cid{i}",
            broker_order_id=f"bo{i}",
            kis_fill_price=kis,
            sim_fill_price=sim,
            kis_fill_qty="10",
            sim_fill_qty="10",
            kis_fill_ts="2026-04-26T09:00:00+00:00",
            sim_fill_ts="2026-04-26T09:00:00+00:00",
            latency_ms=5.0,
        ))
        wal.write(WALEvent(
            ts="2026-04-26T09:00:00+00:00",
            event_type=EVENT_TRACKING_SAMPLE,
            payload=payload,
        ))


def test_aggregate_from_wal_reads_tracking_samples():
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = Path(tmpdir) / "wal.jsonl"
        _write_wal_with_samples(wal_path, [
            ("100.5", "100.0"),
            ("99.5", "100.0"),
            ("101.0", "100.0"),
        ])
        since = datetime(2026, 4, 26, 0, 0, 0, tzinfo=timezone.utc)
        until = datetime(2026, 4, 26, 23, 59, 59, tzinfo=timezone.utc)
        report = aggregate_from_wal(wal_path, since, until)
        assert report.n_samples == 3


# ---------------------------------------------------------------------------
# Gauge emit
# ---------------------------------------------------------------------------

def test_emit_gauge_sets_metric():
    m = Metrics(registry=CollectorRegistry())
    samples = [_make_sample("100.5", "100.0", f"b{i}") for i in range(3)]
    report = compute_tracking_error(samples)
    emit_gauge(m, report, strategy="strat_a")

    gauge_val = None
    for metric in m.paper_kis_tracking_error.collect():
        for s in metric.samples:
            if s.labels.get("strategy") == "strat_a":
                gauge_val = s.value
    assert gauge_val is not None
    assert gauge_val > 0


# ---------------------------------------------------------------------------
# daily_report_row
# ---------------------------------------------------------------------------

def test_daily_report_row_structure():
    samples = [_make_sample("100.5", "100.0", f"b{i}") for i in range(5)]
    report = compute_tracking_error(samples)
    row = daily_report_row("2026-04-26", report)
    assert row["date"] == "2026-04-26"
    assert row["n_samples"] == 5
    assert "mean_tracking_error" in row
    assert "p50" in row
    assert "p95" in row
    assert "exceeds_threshold" in row
