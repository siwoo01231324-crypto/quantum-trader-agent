"""PR #348 — executor 가 REJECTED ack 를 ``order_rejected`` event_type 으로
WAL 에 기록하는지 검증.

이전 ``if ack.status not in ("REJECTED",):`` 가 거부된 주문을 silent skip →
PR #342 (shorts_allowed) 후에도 13884+ sell 시그널 모두 REJECTED 인데 WAL 에
흔적 0건 → 사용자 보고 "거래 안 함" 진단 불가능.

본 fix: REJECTED 도 WAL 에 적되 새 event_type ``order_rejected`` 로 분리해
dashboard /signals follow_up 가 "ordered" 로 잘못 분류 안 함. reject_reason
필드에 사유 (KILL_SWITCH / CONVERSION:... / BROKER_ERROR:... 또는 binance
error code) 함께 기록.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.brokers.base import OrderAck, OrderRequest
from src.live.executor import execute_intents
from src.live.wal import WAL
from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch
from src.portfolio.order_intent import OrderIntent


def _read_wal_events(wal_path: Path) -> list[dict]:
    out: list[dict] = []
    for line in wal_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


class _BrokerRejecting:
    """Broker stub that always returns a REJECTED ack with a reject_reason."""

    name = "binance_futures_async"
    paper = False

    def __init__(self, reason: str = "binance:-2022:reduce_only_with_no_position"):
        self._reason = reason

    async def place_order(self, req: OrderRequest) -> OrderAck:
        return OrderAck(
            broker_order_id="",
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            status="REJECTED",
            ts=datetime.now(timezone.utc),
            reject_reason=self._reason,
        )


class _BrokerNew:
    """Broker stub that returns a normal NEW ack (non-REJECTED)."""

    name = "binance_futures_async"
    paper = False

    async def place_order(self, req: OrderRequest) -> OrderAck:
        return OrderAck(
            broker_order_id="b-1",
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            status="NEW",
            ts=datetime.now(timezone.utc),
            qty=req.qty,
            price=req.price or Decimal("1"),
        )


def _intent(side: str = "sell", symbol: str = "BTCUSDT") -> OrderIntent:
    return OrderIntent(
        strategy_id="live-airborne-bb-reversal-kst-hours",
        symbol=symbol,
        side=side,
        qty=0.001,
        reason="airborne_v12_short_fire:test",
    )


@pytest.mark.asyncio
async def test_rejected_ack_written_to_wal_as_order_rejected(tmp_path):
    wal_path = tmp_path / "wal.jsonl"
    wal = WAL(wal_path)
    broker = _BrokerRejecting()
    await execute_intents(
        [_intent()], broker=broker, kill_switch=KillSwitch(),
        wal=wal, metrics=Metrics(),
    )
    events = _read_wal_events(wal_path)
    rejected = [e for e in events if e.get("event_type") == "order_rejected"]
    acked = [e for e in events if e.get("event_type") == "order_acked"]
    assert len(rejected) == 1, f"REJECTED 가 order_rejected 로 기록돼야 함: {events}"
    assert len(acked) == 0, "REJECTED 가 order_acked 로 잘못 기록되면 안 됨"

    payload = rejected[0]["payload"]
    assert payload["status"] == "REJECTED"
    assert payload["strategy_id"] == "live-airborne-bb-reversal-kst-hours"
    assert payload["side"] == "sell"
    assert payload["symbol"] == "BTCUSDT"
    # 핵심 진단 필드 — reject_reason 이 broker error code 보존
    assert payload["reject_reason"] == "binance:-2022:reduce_only_with_no_position"


@pytest.mark.asyncio
async def test_normal_ack_still_written_as_order_acked(tmp_path):
    """기존 동작 byte-identical — NEW / FILLED 등은 order_acked 그대로."""
    wal_path = tmp_path / "wal.jsonl"
    wal = WAL(wal_path)
    broker = _BrokerNew()
    await execute_intents(
        [_intent(side="buy")], broker=broker, kill_switch=KillSwitch(),
        wal=wal, metrics=Metrics(),
    )
    events = _read_wal_events(wal_path)
    acked = [e for e in events if e.get("event_type") == "order_acked"]
    rejected = [e for e in events if e.get("event_type") == "order_rejected"]
    assert len(acked) == 1, "정상 ack 는 order_acked 로 기록돼야 함"
    assert len(rejected) == 0, "NEW status 가 order_rejected 로 기록되면 안 됨"
    # reject_reason 필드는 정상 ack 의 payload 에 등장하지 않음 (legacy 동작 보존)
    assert "reject_reason" not in acked[0]["payload"]


@pytest.mark.asyncio
async def test_kill_switch_rejection_logged(tmp_path):
    """KILL_SWITCH 거부도 order_rejected 로 기록 (사유 확인 가능)."""
    wal_path = tmp_path / "wal.jsonl"
    wal = WAL(wal_path)
    ks = KillSwitch()
    ks.trip(reason="test reason", source="test")  # kill switch 활성

    await execute_intents(
        [_intent()], broker=_BrokerNew(), kill_switch=ks,
        wal=wal, metrics=Metrics(),
    )
    events = _read_wal_events(wal_path)
    rejected = [e for e in events if e.get("event_type") == "order_rejected"]
    assert len(rejected) == 1
    assert "KILL_SWITCH" in (rejected[0]["payload"].get("reject_reason") or "")
