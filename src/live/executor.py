from __future__ import annotations
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable

from src.brokers.base import AsyncBrokerAdapter, OrderAck
from src.brokers.errors import BrokerError
from src.execution.base import MarketState
from src.live.conversion import intent_to_order_request
from src.live.types import (
    EVENT_ORDER_ACKED,
    EVENT_TRACKING_SAMPLE,
    WALEvent,
)
from src.live.wal import WAL, WALWriteFailed
from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch, KillSwitchTripped
from src.portfolio.order_intent import OrderIntent

if TYPE_CHECKING:
    from src.live.strategy_position_store import StrategyPositionStore

logger = logging.getLogger(__name__)


async def execute_intents(
    intents: Iterable[OrderIntent],
    *,
    broker: AsyncBrokerAdapter,
    kill_switch: KillSwitch,
    wal: WAL,
    metrics: Metrics,
    market_state: MarketState | None = None,
    position_store: "StrategyPositionStore | None" = None,
) -> list[OrderAck]:
    """OrderIntent 시퀀스를 broker 에 전달. Phase 2 전환 seam.

    Phase 2 전환 시 broker 인자만 paper_broker → async_router 로 교체하면 실거래.

    market_state: Architect note #1. None 시 self-sim skip (PaperBroker 단독 호환).

    각 intent 처리 흐름:
      1. kill_switch.assert_allow_order() → tripped 시 REJECTED ack (KILL_SWITCH)
      2. intent_to_order_request 변환 — ValueError (unknown symbol) 시 REJECTED ack (CONVERSION:...)
      3. broker.place_order() 호출 → ack
         - WALWriteFailed 또는 BrokerError 발생 시 catch → REJECTED ack (WAL_WRITE_FAIL / BROKER_ERROR)
      4. 정상 ack 시: order_acked WAL append (Architect note #2)
      5. self-sim tracking_sample (Architect note #3): KIS broker + market_state 제공 시만 실행
      6. 메트릭 기록: orders_total (status 라벨 = ack.status), order_latency_seconds (broker, algo='execute_intents')

    WAL 기록은 broker (PaperBroker) 가 내부에서 처리. executor 는 메트릭 + order_acked/tracking_sample.
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
        except BrokerError as exc:
            # Architect note: BrokerError → REJECTED ack down-grade (시그니처 불변)
            ack = _reject(intent, idempotency_key, f"BROKER_ERROR:{exc}")
        latency = time.monotonic() - t0

        # 4. order_acked WAL append — 정상 ack 만 (Architect note #2)
        # WAL writes serialized via single consumer task (loop.py:167); WS fill listener writes via asyncio.Queue (Stage 5)
        if ack.status not in ("REJECTED",):
            if position_store is not None:
                position_store.register_order(
                    client_order_id=ack.client_order_id,
                    strategy_id=intent.strategy_id,
                )
            ts_now = datetime.now(timezone.utc).isoformat()
            try:
                wal.write(WALEvent(
                    ts=ts_now,
                    event_type=EVENT_ORDER_ACKED,
                    payload={
                        "client_order_id": ack.client_order_id,
                        "broker_order_id": ack.broker_order_id,
                        "ack_ts": ts_now,
                        "status": ack.status,
                        "origin": "executor",
                        "strategy_id": intent.strategy_id,
                    },
                ))
            except WALWriteFailed:
                logger.warning("order_acked WAL write failed for %s", ack.client_order_id)

        # 5. self-sim tracking_sample (Architect note #3)
        # Gate: non-paper broker + market_state provided → sim-vs-sim tautology prevented
        if not getattr(broker, "paper", False) and market_state is not None:
            _write_tracking_sample(wal, req, ack, market_state, intent.strategy_id)

        # 6. 메트릭
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


def _write_tracking_sample(
    wal: WAL,
    req: "OrderRequest",  # noqa: F821 — avoids circular import at runtime
    ack: OrderAck,
    market_state: MarketState,
    strategy_id: str | None = None,
) -> None:
    """Call MockMatchingEngine.match() once and append tracking_sample to WAL.

    kis_fill_* fields are left empty here — backfilled when WS fill arrives (Stage 5).
    """
    from src.execution.mock_matching import MockMatchingEngine

    engine = MockMatchingEngine()
    sim_fills = engine.match(req, market_state)
    if not sim_fills:
        return

    sim_fill = sim_fills[0]
    ts_now = datetime.now(timezone.utc).isoformat()
    try:
        wal.write(WALEvent(
            ts=ts_now,
            event_type=EVENT_TRACKING_SAMPLE,
            payload={
                "client_order_id": req.client_order_id,
                "broker_order_id": ack.broker_order_id,
                "kis_fill_price": "",   # backfilled by WS fill listener (Stage 5)
                "sim_fill_price": str(sim_fill.price),
                "kis_fill_qty": "",
                "sim_fill_qty": str(sim_fill.qty),
                "kis_fill_ts": "",
                "sim_fill_ts": ts_now,
                "latency_ms": 0.0,     # backfilled on join
                "strategy_id": strategy_id,
            },
        ))
    except WALWriteFailed:
        logger.warning("tracking_sample WAL write failed for %s", req.client_order_id)


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
