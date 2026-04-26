"""Tracking error aggregation for KIS paper self-simulation (Stage 5.1, #105).

Computes mean(|kis_fill_price - sim_fill_price| / sim_fill_price) from WAL
tracking_sample events and emits qta_paper_kis_tracking_error Gauge.

Threshold: 0.005 (0.5%). Stage 6 kill-switch R3 monitors a 5-minute window
separately — this module only emits the Gauge, never trips the kill-switch.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from src.live.types import EVENT_TRACKING_SAMPLE, TrackingSamplePayload
from src.live.wal import replay
from src.observability.metrics import Metrics

TRACKING_ERROR_THRESHOLD = Decimal("0.005")


@dataclass(frozen=True)
class TrackingSample:
    broker_order_id: str
    kis_fill_price: Decimal
    sim_fill_price: Decimal


@dataclass
class TrackingErrorReport:
    n_samples: int
    n_missing: int
    mean_tracking_error: Decimal
    p50: float | None
    p95: float | None
    exceeds_threshold: bool


def compute_tracking_error(
    samples: Sequence[TrackingSample],
    missing_count: int = 0,
) -> TrackingErrorReport:
    """Compute tracking error report from a list of TrackingSample objects.

    Missing fills (KIS WS drop) are excluded from the calculation but counted.
    """
    if not samples:
        return TrackingErrorReport(
            n_samples=0,
            n_missing=missing_count,
            mean_tracking_error=Decimal("0"),
            p50=None,
            p95=None,
            exceeds_threshold=False,
        )

    errors: list[float] = []
    for s in samples:
        if s.sim_fill_price == 0:
            continue
        rel_error = abs(s.kis_fill_price - s.sim_fill_price) / s.sim_fill_price
        errors.append(float(rel_error))

    if not errors:
        return TrackingErrorReport(
            n_samples=len(samples),
            n_missing=missing_count,
            mean_tracking_error=Decimal("0"),
            p50=None,
            p95=None,
            exceeds_threshold=False,
        )

    mean_err = Decimal(str(statistics.mean(errors)))
    sorted_errors = sorted(errors)
    n = len(sorted_errors)
    p50 = sorted_errors[int(n * 0.50)]
    p95 = sorted_errors[min(int(n * 0.95), n - 1)]

    return TrackingErrorReport(
        n_samples=len(samples),
        n_missing=missing_count,
        mean_tracking_error=mean_err,
        p50=p50,
        p95=p95,
        exceeds_threshold=mean_err > TRACKING_ERROR_THRESHOLD,
    )


def aggregate_from_wal(
    wal_path: Path,
    since: datetime,
    until: datetime,
) -> TrackingErrorReport:
    """Read WAL and aggregate tracking_sample events in [since, until]."""
    events, _ = replay(wal_path)
    samples: list[TrackingSample] = []
    for ev in events:
        if ev.event_type != EVENT_TRACKING_SAMPLE:
            continue
        try:
            # Parse ISO 8601 ts; compare in UTC
            ev_dt = datetime.fromisoformat(ev.ts)
            if ev_dt.tzinfo is not None:
                since_aware = since.replace(tzinfo=since.tzinfo or ev_dt.tzinfo)
                until_aware = until.replace(tzinfo=until.tzinfo or ev_dt.tzinfo)
            else:
                since_aware = since
                until_aware = until
            if not (since_aware <= ev_dt <= until_aware):
                continue
            p = ev.payload
            samples.append(TrackingSample(
                broker_order_id=p["broker_order_id"],
                kis_fill_price=Decimal(p["kis_fill_price"]),
                sim_fill_price=Decimal(p["sim_fill_price"]),
            ))
        except (KeyError, ValueError):
            continue
    return compute_tracking_error(samples)


def emit_gauge(metrics: Metrics, report: TrackingErrorReport, strategy: str = "unknown") -> None:
    """Emit qta_paper_kis_tracking_error Gauge with the computed mean error."""
    metrics.paper_kis_tracking_error.labels(strategy=strategy).set(
        float(report.mean_tracking_error)
    )


def daily_report_row(date: str, report: TrackingErrorReport) -> dict:
    """Return a dict row suitable for a daily report."""
    return {
        "date": date,
        "n_samples": report.n_samples,
        "n_missing": report.n_missing,
        "mean_tracking_error": float(report.mean_tracking_error),
        "p50": report.p50,
        "p95": report.p95,
        "exceeds_threshold": report.exceeds_threshold,
    }
