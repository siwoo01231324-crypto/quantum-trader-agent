from __future__ import annotations
from datetime import datetime, timezone

from src.brokers.base import OrderAck


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_default_reject_reason_none():
    ack = OrderAck(
        broker_order_id="1",
        client_order_id="1",
        symbol="X",
        status="FILLED",
        ts=_now(),
    )
    assert ack.reject_reason is None


def test_reject_reason_set():
    ack = OrderAck(
        broker_order_id="2",
        client_order_id="2",
        symbol="BTCUSDT",
        status="REJECTED",
        ts=_now(),
        reject_reason="WAL_WRITE_FAIL",
    )
    assert ack.reject_reason == "WAL_WRITE_FAIL"


def test_legacy_positional_args_still_work():
    ack = OrderAck("oid", "cid", "BTCUSDT", "REJECTED", datetime.now(timezone.utc))
    assert ack.reject_reason is None
    assert ack.broker_order_id == "oid"
    assert ack.client_order_id == "cid"
