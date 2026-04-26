from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class OrderStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class WALEvent:
    """WAL JSONL 레코드. ts 는 시스템 UTC ISO 8601 (예: 2026-04-25T09:00:00.123456+00:00)."""

    ts: str
    event_type: str
    schema_version: int = 1
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class WALCorruption:
    """WAL replay 시 파싱에 실패한 손상 라인의 메타."""

    line_no: int
    raw: str
    error: str


@dataclass(frozen=True)
class Tick:
    """실시간 체결 틱. ts=수신 시각(UTC), server_ts=WS 서버 시간(별도 필드)."""

    symbol: str
    price: Decimal
    qty: Decimal
    ts: str
    server_ts: str | None = None


# ---------------------------------------------------------------------------
# Phase 2 WAL event payload types (#105, Stage 4.3)
#
# WAL replay ignores unknown event_type values — adding these types is
# backward-compatible. Existing event_type signatures are unchanged.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrderAckedPayload:
    """Payload for event_type='order_acked' (Architect note #2).

    Written by executor immediately after broker ack. Provides the join key
    (broker_order_id) needed to correlate WS fill notifications.
    origin='executor' distinguishes this from PaperBroker's 'order_submitted'.
    """

    client_order_id: str
    broker_order_id: str
    ack_ts: str          # UTC ISO 8601
    status: str          # OrderStatus value
    origin: str = "executor"


@dataclass(frozen=True)
class TrackingSamplePayload:
    """Payload for event_type='tracking_sample' (AC4 self-sim, Architect note #3).

    Written once per KIS fill: executor calls MockMatchingEngine.match() on the
    same (req, market_state) after receiving the KIS ack, then appends this row.
    Used by tracking_error.py to compute mean(|kis - sim| / sim).
    """

    client_order_id: str
    broker_order_id: str
    kis_fill_price: str   # Decimal-serialised string for JSON safety
    sim_fill_price: str
    kis_fill_qty: str
    sim_fill_qty: str
    kis_fill_ts: str      # UTC ISO 8601
    sim_fill_ts: str      # UTC ISO 8601
    latency_ms: float


@dataclass(frozen=True)
class FillAnomalyPayload:
    """Payload for event_type='fill_anomaly'.

    Recorded when a partial fill is detected or broker_order_id cannot be
    matched to a known order_acked record. Does NOT trigger a kill-switch;
    counted by qta_kis_fill_missing_total.
    """

    client_order_id: str
    broker_order_id: str
    reason: str          # e.g. "partial_fill" | "unmatched_broker_id"
    fill_ts: str         # UTC ISO 8601
    fill_qty: str        # Decimal-serialised


# Canonical event_type string constants for use in WALEvent.event_type
EVENT_ORDER_ACKED = "order_acked"
EVENT_TRACKING_SAMPLE = "tracking_sample"
EVENT_FILL_ANOMALY = "fill_anomaly"
