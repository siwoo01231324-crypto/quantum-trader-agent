"""Canonical event_type 상수 — 매매 타임라인 4단계 (#181).

WAL JSONL 의 `event_type` 필드와 WS `/ws/timeline` 페이로드의
`event_type` 필드에 사용한다.
"""
from __future__ import annotations

EVENT_SIGNAL_EMITTED = "signal_emitted"
EVENT_METALABELER_DECISION = "metalabeler_decision"
EVENT_ORDER_PLACED = "order_placed"
EVENT_FILL_RECEIVED = "fill_received"

TIMELINE_EVENT_TYPES: frozenset[str] = frozenset({
    EVENT_SIGNAL_EMITTED,
    EVENT_METALABELER_DECISION,
    EVENT_ORDER_PLACED,
    EVENT_FILL_RECEIVED,
})
