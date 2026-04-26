from __future__ import annotations
import logging
import time
from datetime import datetime, timezone
from typing import Iterable

from src.brokers.base import AsyncBrokerAdapter, OrderAck
from src.live.conversion import intent_to_order_request
from src.live.wal import WAL, WALWriteFailed
from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch, KillSwitchTripped
from src.portfolio.order_intent import OrderIntent

logger = logging.getLogger(__name__)


async def execute_intents(
    intents: Iterable[OrderIntent],
    *,
    broker: AsyncBrokerAdapter,
    kill_switch: KillSwitch,
    wal: WAL,
    metrics: Metrics,
) -> list[OrderAck]:
    """OrderIntent 시퀀스를 broker 에 전달. Phase 2 전환 seam.

    Phase 2 전환 시 broker 인자만 paper_broker → async_router 로 교체하면 실거래.

    각 intent 처리 흐름:
      1. kill_switch.assert_allow_order() → tripped 시 REJECTED ack (KILL_SWITCH)
      2. intent_to_order_request 변환 — ValueError (unknown symbol) 시 REJECTED ack (CONVERSION:...)
      3. broker.place_order() 호출 → ack
         - WALWriteFailed 발생 시 catch → REJECTED ack (WAL_WRITE_FAIL)
      4. 메트릭 기록: orders_total (status 라벨 = ack.status), order_latency_seconds (broker, algo='execute_intents')

    WAL 기록은 broker (PaperBroker) 가 내부에서 처리. executor 는 메트릭만.
    """
    acks: list[OrderAck] = []
    intents_list = list(intents)
    for idx, intent in enumerate(intents_list):
        idempotency_key = _make_key(intent, idx)

        # 1. Kill switch 게이트
        try:
            kill_switch.assert_allow_order()
        except KillSwitchTripped:
            ack = _reject(intent, idempotency_key, "KILL_SWITCH")
            acks.append(ack)
            metrics.orders_total.labels(
                strategy=intent.strategy_id,
                broker=broker.name,
                side=intent.side.upper(),
                status="REJECTED",
            ).inc()
            continue

        # 2. 변환
        try:
            req = intent_to_order_request(intent, idempotency_key=idempotency_key)
        except ValueError as err:
            ack = _reject(intent, idempotency_key, f"CONVERSION:{err}")
            acks.append(ack)
            metrics.orders_total.labels(
                strategy=intent.strategy_id,
                broker=broker.name,
                side=intent.side.upper(),
                status="REJECTED",
            ).inc()
            continue

        # 3. broker 호출 + latency 메트릭
        t0 = time.monotonic()
        try:
            ack = await broker.place_order(req)
        except WALWriteFailed:
            ack = _reject(intent, idempotency_key, "WAL_WRITE_FAIL")
        latency = time.monotonic() - t0

        # 4. 메트릭
        metrics.order_latency_seconds.labels(
            broker=broker.name,
            algo="execute_intents",
        ).observe(latency)
        metrics.orders_total.labels(
            strategy=intent.strategy_id,
            broker=broker.name,
            side=intent.side.upper(),
            status=ack.status,
        ).inc()
        acks.append(ack)

    return acks


def _make_key(intent: OrderIntent, idx: int) -> str:
    """idempotency-key: f'{strategy_id}:{symbol}:{ts_epoch_ms}:{idx}'."""
    ts_ms = int(time.time() * 1000)
    return f"{intent.strategy_id}:{intent.symbol}:{ts_ms}:{idx}"


def _reject(intent: OrderIntent, key: str, reason: str) -> OrderAck:
    return OrderAck(
        broker_order_id="",
        client_order_id=key,
        symbol=intent.symbol,
        status="REJECTED",
        ts=datetime.now(timezone.utc),
        reject_reason=reason,
    )
