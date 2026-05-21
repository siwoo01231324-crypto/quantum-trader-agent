"""Canonical event_type 상수 — 매매 타임라인 4단계 (#181).

WAL JSONL 의 `event_type` 필드와 WS `/ws/timeline` 페이로드의
`event_type` 필드에 사용한다.
"""
from __future__ import annotations

EVENT_SIGNAL_EMITTED = "signal_emitted"
EVENT_METALABELER_DECISION = "metalabeler_decision"
EVENT_ORDER_PLACED = "order_placed"
EVENT_FILL_RECEIVED = "fill_received"

# 수동 계좌 거래 (Claude Routines 일일 리포트 분석 대상, 2026-05-21).
# 사용자가 대시보드 폼으로 직접 입력 — 자동 fill 과 다른 event_type 으로 구분.
EVENT_MANUAL_TRADE = "manual_trade"

TIMELINE_EVENT_TYPES: frozenset[str] = frozenset({
    EVENT_SIGNAL_EMITTED,
    EVENT_METALABELER_DECISION,
    EVENT_ORDER_PLACED,
    EVENT_FILL_RECEIVED,
    EVENT_MANUAL_TRADE,
})
