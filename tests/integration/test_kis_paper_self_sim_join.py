"""Stage 4.4: KIS paper self-sim join accuracy regression (Architect note #4, #105).

Tests order_acked → tracking_sample join via broker_order_id:
  - 3 normal fills (kis_fill_price backfilled, join succeeds)
  - 1 partial fill (fill_anomaly WAL event written, qta_kis_partial_fill_total inc)
  - 1 missing fill (no KIS WS event, qta_kis_fill_missing_total inc, excluded from tracking_error)

Also verifies:
  - tracking_error.compute_tracking_error uses only successfully joined samples
  - broker_order_id join key works even when client_order_id is empty (KIS WS issue)
"""
from __future__ import annotations

import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry

from src.live.tracking_error import TrackingSample, compute_tracking_error
from src.live.types import (
    EVENT_FILL_ANOMALY,
    EVENT_ORDER_ACKED,
    EVENT_TRACKING_SAMPLE,
    WALEvent,
)
from src.live.wal import WAL, replay
from src.observability.metrics import Metrics


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_order_acked(wal: WAL, broker_order_id: str, status: str = "NEW") -> None:
    wal.write(WALEvent(
        ts=_ts(),
        event_type=EVENT_ORDER_ACKED,
        payload={
            "client_order_id": f"cid_{broker_order_id}",
            "broker_order_id": broker_order_id,
            "ack_ts": _ts(),
            "status": status,
            "origin": "executor",
        },
    ))


def _write_tracking_sample(
    wal: WAL, broker_order_id: str, kis_price: str, sim_price: str
) -> None:
    wal.write(WALEvent(
        ts=_ts(),
        event_type=EVENT_TRACKING_SAMPLE,
        payload={
            "client_order_id": f"cid_{broker_order_id}",
            "broker_order_id": broker_order_id,
            "kis_fill_price": kis_price,
            "sim_fill_price": sim_price,
            "kis_fill_qty": "10",
            "sim_fill_qty": "10",
            "kis_fill_ts": _ts(),
            "sim_fill_ts": _ts(),
            "latency_ms": 5.0,
        },
    ))


def _write_fill_anomaly(wal: WAL, broker_order_id: str, reason: str) -> None:
    wal.write(WALEvent(
        ts=_ts(),
        event_type=EVENT_FILL_ANOMALY,
        payload={
            "client_order_id": "",
            "broker_order_id": broker_order_id,
            "reason": reason,
            "fill_ts": _ts(),
            "fill_qty": "5",
        },
    ))


# ---------------------------------------------------------------------------
# 1. 3 normal + 1 partial + 1 missing → join accuracy
# ---------------------------------------------------------------------------

def test_self_sim_join_3_normal_1_partial_1_missing():
    """5 orders: 3 normal fills, 1 partial fill, 1 missing.
    Only 3 normal fills should contribute to tracking error.
    Partial fill recorded as fill_anomaly. Missing fill increments kis_fill_missing_total.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = Path(tmpdir) / "wal.jsonl"
        wal = WAL(wal_path)
        m = Metrics(registry=CollectorRegistry())

        # 3 normal fills: write order_acked + tracking_sample (both kis + sim prices)
        normal_pairs = [
            ("BO001", "70000", "70050"),
            ("BO002", "69500", "69480"),
            ("BO003", "70200", "70150"),
        ]
        for bid, kis_p, sim_p in normal_pairs:
            _write_order_acked(wal, bid, "FILLED")
            _write_tracking_sample(wal, bid, kis_p, sim_p)

        # 1 partial fill: order_acked + fill_anomaly (no tracking_sample)
        _write_order_acked(wal, "BO004", "PARTIALLY_FILLED")
        _write_fill_anomaly(wal, "BO004", "partial_fill")
        m.kis_partial_fill_total.labels(strategy="test").inc()

        # 1 missing fill: order_acked NEW but no KIS WS event
        _write_order_acked(wal, "BO005", "NEW")
        m.kis_fill_missing_total.labels(strategy="test").inc()

        # Replay WAL and join tracking_samples by broker_order_id
        events, corruptions = replay(wal_path)
        assert len(corruptions) == 0

        acked_bids = {
            ev.payload["broker_order_id"]: ev.payload["status"]
            for ev in events
            if ev.event_type == EVENT_ORDER_ACKED
        }
        tracking_samples_by_bid = {
            ev.payload["broker_order_id"]: ev.payload
            for ev in events
            if ev.event_type == EVENT_TRACKING_SAMPLE
        }
        anomaly_bids = {
            ev.payload["broker_order_id"]
            for ev in events
            if ev.event_type == EVENT_FILL_ANOMALY
        }

        # Join: only broker_order_ids in both acked + tracking_samples
        joined_samples = []
        for bid, payload in tracking_samples_by_bid.items():
            if payload["kis_fill_price"] and payload["sim_fill_price"]:
                joined_samples.append(TrackingSample(
                    broker_order_id=bid,
                    kis_fill_price=Decimal(payload["kis_fill_price"]),
                    sim_fill_price=Decimal(payload["sim_fill_price"]),
                ))

        assert len(joined_samples) == 3, f"Expected 3 joined samples, got {len(joined_samples)}"
        assert "BO004" in anomaly_bids, "Partial fill must be recorded as fill_anomaly"
        assert "BO005" not in tracking_samples_by_bid, "Missing fill must have no tracking_sample"

        # Verify metrics incremented
        partial_total = sum(
            s.value
            for metric in m.kis_partial_fill_total.collect()
            for s in metric.samples
        )
        assert partial_total >= 1

        missing_total = sum(
            s.value
            for metric in m.kis_fill_missing_total.collect()
            for s in metric.samples
        )
        assert missing_total >= 1


# ---------------------------------------------------------------------------
# 2. broker_order_id join key works when client_order_id is empty (KIS WS issue)
# ---------------------------------------------------------------------------

def test_join_by_broker_order_id_when_client_id_empty():
    """KIS WS fill sometimes has client_order_id=''. Join must use broker_order_id."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = Path(tmpdir) / "wal.jsonl"
        wal = WAL(wal_path)

        # order_acked with normal client_order_id
        wal.write(WALEvent(
            ts=_ts(),
            event_type=EVENT_ORDER_ACKED,
            payload={
                "client_order_id": "cid_X",
                "broker_order_id": "BOXYZ",
                "ack_ts": _ts(),
                "status": "FILLED",
                "origin": "executor",
            },
        ))
        # tracking_sample with empty client_order_id (KIS WS issue), broker_order_id present
        wal.write(WALEvent(
            ts=_ts(),
            event_type=EVENT_TRACKING_SAMPLE,
            payload={
                "client_order_id": "",   # empty — KIS WS limitation
                "broker_order_id": "BOXYZ",
                "kis_fill_price": "70000",
                "sim_fill_price": "70000",
                "kis_fill_qty": "10",
                "sim_fill_qty": "10",
                "kis_fill_ts": _ts(),
                "sim_fill_ts": _ts(),
                "latency_ms": 3.0,
            },
        ))

        events, _ = replay(wal_path)
        tracking = {
            ev.payload["broker_order_id"]: ev.payload
            for ev in events
            if ev.event_type == EVENT_TRACKING_SAMPLE
        }
        assert "BOXYZ" in tracking, "broker_order_id join must succeed even with empty client_order_id"
        assert tracking["BOXYZ"]["client_order_id"] == "", "client_order_id should be empty as written"


# ---------------------------------------------------------------------------
# 3. compute_tracking_error excludes missing fills from formula
# ---------------------------------------------------------------------------

def test_tracking_error_excludes_missing_fills():
    """3 valid samples + 1 simulated missing (n_missing=1) → error computed over 3 only."""
    samples = [
        TrackingSample("BO001", Decimal("70000"), Decimal("70000")),
        TrackingSample("BO002", Decimal("70700"), Decimal("70000")),
        TrackingSample("BO003", Decimal("69300"), Decimal("70000")),
    ]
    report = compute_tracking_error(samples, missing_count=1)
    assert report.n_samples == 3
    assert report.n_missing == 1
    # mean(|70000-70000|/70000, |70700-70000|/70000, |69300-70000|/70000)
    # = mean(0, 0.01, 0.01) = 0.00666...
    expected = (0.0 + 0.01 + 0.01) / 3
    assert abs(float(report.mean_tracking_error) - expected) < 1e-9
