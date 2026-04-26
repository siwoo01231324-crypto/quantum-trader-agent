from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.live.types import OrderStatus, Tick, WALCorruption, WALEvent


def test_order_status_str_enum():
    assert OrderStatus.SUBMITTED.value == "SUBMITTED"
    assert OrderStatus.FILLED.value == "FILLED"
    assert OrderStatus.PARTIALLY_FILLED.value == "PARTIALLY_FILLED"
    assert OrderStatus.REJECTED.value == "REJECTED"
    assert OrderStatus.CANCELLED.value == "CANCELLED"


def test_wal_event_frozen():
    event = WALEvent(ts="2026-04-25T00:00:00+00:00", event_type="order_submitted")
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.ts = "x"  # type: ignore[misc]


def test_wal_event_default_schema_version():
    event = WALEvent(ts="2026-04-25T00:00:00+00:00", event_type="order_filled")
    assert event.schema_version == 1


def test_wal_event_ts_isoformat_round_trip():
    now = datetime.now(timezone.utc)
    ts_str = now.isoformat()
    event = WALEvent(ts=ts_str, event_type="order_submitted")
    parsed = datetime.fromisoformat(event.ts)
    assert parsed.tzinfo is not None
    assert parsed == now


def test_tick_server_ts_optional():
    tick = Tick(
        symbol="BTCUSDT",
        price=Decimal("50000"),
        qty=Decimal("0.001"),
        ts="2026-04-25T09:00:00+00:00",
    )
    assert tick.server_ts is None


def test_wal_corruption_fields():
    corruption = WALCorruption(line_no=3, raw='{"bad": json}', error="JSONDecodeError")
    assert corruption.line_no == 3
    assert corruption.raw == '{"bad": json}'
    assert corruption.error == "JSONDecodeError"
