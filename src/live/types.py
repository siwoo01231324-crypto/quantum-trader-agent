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
