from __future__ import annotations
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Callable, Iterable

from src.brokers import client_id as client_id_mod
from src.brokers.base import AsyncBrokerAdapter, OrderAck, OrderRequest, OrderType
from src.brokers.errors import BrokerError
from src.execution.base import MarketState, TimeInForce
from src.live.conversion import intent_to_order_request
from src.live.post_only_fallback import (
    is_post_only,
    resubmit_post_only_as_market,
    schedule_post_only_fallback,
)
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

# post-only Maker 진입 limit 가격 오프셋 (post-only-maker-entry.draft.md 2단계).
# buy 는 기준가보다 0.05% 아래, sell 은 0.05% 위로 발주해 taker 가 되지 않도록
# (= maker 보장) 한다. tick-size 정렬은 broker adapter 가 담당.
_POST_ONLY_OFFSET = Decimal("0.0005")


def _target_leverage() -> int | None:
    """``QTA_TARGET_LEVERAGE`` env → 양의 int 면 그 값, 아니면 None.

    #380 — 설정 시 executor 가 발주 직전 ``broker.ensure_leverage_target(symbol,
    N)`` 으로 leverage 를 *강제* (브로커 UI 수동설정 의존 제거. 데모+실계좌
    동시 운영 시 UI 동기화가 어려워 코드가 leverage 의 truth source). 미설정 /
    0 / 비정수면 None → 기존 ``ensure_leverage_minimum`` (1x 안전망) 경로 그대로.
    """
    raw = os.environ.get("QTA_TARGET_LEVERAGE", "").strip()
    if not raw:
        return None
    try:
        v = int(raw)
    except ValueError:
        return None
    return v if v > 0 else None


async def execute_intents(
    intents: Iterable[OrderIntent],
    *,
    broker: AsyncBrokerAdapter,
    kill_switch: KillSwitch,
    wal: WAL,
    metrics: Metrics,
    market_state: MarketState | None = None,
    position_store: "StrategyPositionStore | None" = None,
    on_entry_unfilled: Callable[[str, str], None] | None = None,
) -> list[OrderAck]:
    """OrderIntent 시퀀스를 broker 에 전달. Phase 2 전환 seam.

    Phase 2 전환 시 broker 인자만 paper_broker → async_router 로 교체하면 실거래.

    market_state: Architect note #1. None 시 self-sim skip (PaperBroker 단독 호환).

    on_entry_unfilled: post-only Maker 진입(2~4단계)이 완전 미체결 + 시장가
      재발주도 REJECTED 일 때 호출되는 ``(strategy_id, symbol) -> None`` 콜백.
      ``orchestrator.sync_live_entered(sid, sym, 0)`` 에 연결돼 ``_live_entered``
      박제를 푼다. None → post-only 강등(market) 시 그대로, legacy 동작 무영향.

    각 intent 처리 흐름:
      1. kill_switch.assert_allow_order() → tripped 시 REJECTED ack (KILL_SWITCH)
      2. _build_order_request 변환 — MARKET, 또는 post-only 진입이면 GTX LIMIT.
         ValueError (unknown symbol / LIMIT price 누락) 시 REJECTED ack (CONVERSION:...)
      3. broker.place_order() 호출 → ack
         - WALWriteFailed 또는 BrokerError 발생 시 catch → REJECTED ack (WAL_WRITE_FAIL / BROKER_ERROR)
      4. 정상 ack 시: order_acked WAL append (Architect note #2)
      5. self-sim tracking_sample (Architect note #3): KIS broker + market_state 제공 시만 실행
      6. 메트릭 기록: orders_total (status 라벨 = ack.status), order_latency_seconds (broker, algo='execute_intents')
      7. post-only Maker fallback (post-only-maker-entry.draft.md 3단계):
         GTX LIMIT 이 EXPIRED → 즉시 시장가 재발주, NEW/PARTIALLY_FILLED →
         background task 로 미체결분 fallback (loop 비블로킹).

    WAL 기록은 broker (PaperBroker) 가 내부에서 처리. executor 는 메트릭 + order_acked/tracking_sample.
    """
    acks: list[OrderAck] = []
    intents_list = list(intents)
    # PR #348 — REJECTED ack 도 WAL 에 기록 (이전 silent skip). 사유는
    # ``ack.reject_reason`` 에. 새 event_type ``order_rejected`` 로 분리 →
    # dashboard /signals follow_up 가 "ordered" 로 잘못 분류 안 함.
    def _write_rejected_wal(intent_: OrderIntent, ack_: OrderAck) -> None:
        ts_now_ = datetime.now(timezone.utc).isoformat()
        try:
            wal.write(WALEvent(
                ts=ts_now_,
                event_type="order_rejected",
                payload={
                    "client_order_id": ack_.client_order_id,
                    "broker_order_id": ack_.broker_order_id,
                    "ack_ts": ts_now_,
                    "status": ack_.status,
                    "origin": "executor",
                    "strategy_id": intent_.strategy_id,
                    "symbol": intent_.symbol,
                    "side": (
                        intent_.side.value if hasattr(intent_.side, "value")
                        else str(intent_.side)
                    ),
                    "qty": str(intent_.qty),
                    "broker": getattr(broker, "name", ""),
                    "reject_reason": ack_.reject_reason,
                },
            ))
        except WALWriteFailed:
            logger.warning(
                "order_rejected WAL write failed for %s",
                ack_.client_order_id,
            )

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
            _write_rejected_wal(intent, ack)
            continue

        # 2. 변환 — MARKET, 또는 post-only 진입이면 GTX LIMIT.
        try:
            req = _build_order_request(intent, idempotency_key)
        except ValueError as err:
            ack = _reject(intent, idempotency_key, f"CONVERSION:{err}")
            acks.append(ack)
            metrics.orders_total.labels(
                strategy=intent.strategy_id,
                broker=broker.name,
                side=intent.side.upper(),
                status="REJECTED",
            ).inc()
            _write_rejected_wal(intent, ack)
            continue

        # 3. broker 호출 + latency 메트릭
        # #238 Bug B — register the coid→strategy map BEFORE place_order, keyed
        # on the EXACT coid we built (req.client_order_id == idempotency_key).
        # _make_key now yields a Binance-valid coid so the adapter keeps it
        # as-is (no opaque-sha256 re-generation), giving the invariant
        # `registered == submitted == returned-fill coid`. Registering up front
        # (vs. on the ack) makes StrategyPositionStore's explicit map authoritative
        # even when the fill arrives out-of-band via the WS stream — and is
        # harmless on a REJECTED ack (a map entry never yields a position
        # without a fill).
        if position_store is not None:
            position_store.register_order(
                client_order_id=req.client_order_id,
                strategy_id=intent.strategy_id,
            )
            # Additive companion (signature of register_order intentionally
            # unchanged). The Binance user-data BrokerFill carries only the
            # coid + qty/price/fee — NOT symbol/side — so the live fill
            # consumer (src/live/fill_consumer.py) resolves the full context
            # from here to assemble a complete order_filled WAL payload.
            # register_order_context may be absent on a minimal test double;
            # guard so the legacy/no-context path is byte-identical.
            register_ctx = getattr(
                position_store, "register_order_context", None
            )
            if register_ctx is not None:
                register_ctx(
                    client_order_id=req.client_order_id,
                    symbol=req.symbol,
                    side=req.side.value,
                    strategy_id=intent.strategy_id,
                )

        # PR #349 — Binance Futures 의 -1109 "Invalid account" 거부는 *해당
        # 종목 leverage 가 한 번도 설정 안 된* 계정의 첫 발주에서 발생.
        # ``ensure_leverage_minimum`` (broker 어댑터에 있을 때만) 이 종목당
        # 1회만 REST 호출해 leverage 가 0/미설정이면 1x 로 set. 사용자가 web
        # 에서 설정한 값은 *override 하지 않음* (leverage > 0 면 no-op).
        # 어댑터 내부 캐시 (``_leverage_minimum_done``) 가 재호출 폭주 차단.
        # KIS / PaperBroker 는 본 메서드 미지원 → getattr fallback 으로 skip.
        # #380 — QTA_TARGET_LEVERAGE 설정 시 leverage 를 그 값으로 *강제*
        # (ensure_leverage_target). 미설정이면 기존 ensure_leverage_minimum
        # (1x 안전망) 경로 — legacy 동작 byte-identical.
        target_lev = _target_leverage()
        ensure_target = (
            getattr(broker, "ensure_leverage_target", None) if target_lev else None
        )
        if ensure_target is not None:
            try:
                await ensure_target(req.symbol, target_lev)
            except Exception as err:  # noqa: BLE001 — leverage 못 set 해도 발주 시도
                logger.debug(
                    "ensure_leverage_target failed for %s lev=%s: %s — proceed",
                    req.symbol, target_lev, err,
                )
        else:
            ensure_min = getattr(broker, "ensure_leverage_minimum", None)
            if ensure_min is not None:
                try:
                    await ensure_min(req.symbol)
                except Exception as err:  # noqa: BLE001 — leverage 못 set 해도 발주 시도
                    logger.debug(
                        "ensure_leverage_minimum failed for %s: %s — proceed",
                        req.symbol, err,
                    )

        t0 = time.monotonic()
        try:
            ack = await broker.place_order(req)
        except WALWriteFailed:
            ack = _reject(intent, idempotency_key, "WAL_WRITE_FAIL")
        except BrokerError as exc:
            # Architect note: BrokerError → REJECTED ack down-grade (시그니처 불변)
            ack = _reject(intent, idempotency_key, f"BROKER_ERROR:{exc}")
        latency = time.monotonic() - t0

        # 4. WAL append — Architect note #2
        # WAL writes serialized via single consumer task (loop.py:167);
        # WS fill listener writes via asyncio.Queue (Stage 5)
        ts_now = datetime.now(timezone.utc).isoformat()
        if ack.status == "REJECTED":
            # 2026-06-02 PR #348 — REJECTED 도 WAL 에 기록 (별도 event_type).
            # 이전: silent skip → 13884+ sell 시그널이 모두 REJECTED 인데 WAL
            # 흔적 0 → 사용자 보고 "거래 안 함" 진단 불가능 (PR #342 의 same
            # incident). 새 event_type ``order_rejected`` 로 분리해 dashboard
            # /signals follow_up 로직이 "ordered" 로 잘못 분류 안 함.
            try:
                wal.write(WALEvent(
                    ts=ts_now,
                    event_type="order_rejected",
                    payload={
                        "client_order_id": ack.client_order_id,
                        "broker_order_id": ack.broker_order_id,
                        "ack_ts": ts_now,
                        "status": ack.status,
                        "origin": "executor",
                        "strategy_id": intent.strategy_id,
                        "symbol": intent.symbol,
                        "side": (
                            intent.side.value if hasattr(intent.side, "value")
                            else str(intent.side)
                        ),
                        "qty": str(intent.qty),
                        "broker": getattr(broker, "name", ""),
                        # 핵심 진단 필드. KILL_SWITCH / CONVERSION:... /
                        # BROKER_ERROR:... / WAL_WRITE_FAIL 또는 binance
                        # error code (-2022 reduce_only / -2010 invalid order 등).
                        "reject_reason": ack.reject_reason,
                    },
                ))
            except WALWriteFailed:
                logger.warning(
                    "order_rejected WAL write failed for %s",
                    ack.client_order_id,
                )
        else:
            # 기존 동작 byte-identical (NEW / FILLED / PARTIALLY_FILLED 등).
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
                        # #238 — dashboard /api/trades + ops 카드용 informational
                        # fields. status=FILLED 이면 ops_counters 가 fill 로 카운트.
                        "symbol": intent.symbol,
                        "side": intent.side.value if hasattr(intent.side, "value") else str(intent.side),
                        "qty": str(intent.qty),
                        "broker": getattr(broker, "name", ""),
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

        # 7. post-only Maker 진입 fallback (post-only-maker-entry.draft.md 3단계).
        # GTX LIMIT 은 즉시 체결되지 않는다:
        #   EXPIRED — taker 가 될 주문이라 거래소가 거부 → 즉시 시장가 재발주.
        #   NEW/PARTIALLY_FILLED — 호가창 안착 → background task 로 미체결분
        #     fallback (절대 tick loop 을 블록하지 않음).
        # REJECTED 는 fallback 대상 아님 (애초에 발주 실패).
        if is_post_only(req) and ack.status not in ("REJECTED",):
            if ack.status == "EXPIRED":
                await resubmit_post_only_as_market(
                    intent,
                    qty=ack.qty if ack.qty is not None else req.qty,
                    already_filled=Decimal("0"),
                    broker=broker,
                    kill_switch=kill_switch,
                    wal=wal,
                    metrics=metrics,
                    market_state=market_state,
                    position_store=position_store,
                    on_entry_unfilled=on_entry_unfilled,
                )
            elif ack.status in ("NEW", "PARTIALLY_FILLED"):
                schedule_post_only_fallback(
                    intent,
                    req,
                    broker=broker,
                    kill_switch=kill_switch,
                    wal=wal,
                    metrics=metrics,
                    market_state=market_state,
                    position_store=position_store,
                    on_entry_unfilled=on_entry_unfilled,
                )

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
    """Binance-valid deterministic client_order_id (#238 Bug B).

    Previously this returned ``f'{strategy_id}:{symbol}:{ts_ms}:{idx}'``. For a
    real long strategy (e.g. ``live-breakout-with-atr-stop``, 27 chars) that
    string exceeds Binance's 36-char ``newClientOrderId`` cap, so the adapter
    silently discarded the strategy and submitted an opaque sha256 — the
    returned fill could then no longer be attributed to a strategy (per-strategy
    positions / trade-history / pnl all lost). Strategy names are too long to
    cram ``{strategy}:{symbol}:{ts}:{idx}`` into 36 chars, so instead we emit
    the Binance-valid sha256 coid HERE, register THAT exact coid → strategy_id
    in StrategyPositionStore (explicit map — no prefix parsing needed), and the
    adapter keeps an already-valid coid as-is. ``client_id.generate`` is
    deterministic in its inputs, so retrying the same intent in the same
    millisecond yields the same coid (idempotent). ``idx`` is folded into the
    side component so multiple intents emitted in one batch stay distinct.
    """
    ts_ms = int(time.time() * 1000)
    return client_id_mod.generate(
        strategy=intent.strategy_id,
        symbol=intent.symbol,
        side=f"{intent.side}:{idx}",
        ts_ms=ts_ms,
    )


def _reject(intent: OrderIntent, key: str, reason: str) -> OrderAck:
    return OrderAck(
        broker_order_id="",
        client_order_id=key,
        symbol=intent.symbol,
        status="REJECTED",
        ts=datetime.now(timezone.utc),
        reject_reason=reason,
    )


def _post_only_limit_price(side: str, ref_price: float) -> Decimal:
    """Maker 보장 limit 가격 산출 (post-only-maker-entry.draft.md 2단계).

    buy 는 기준가보다 0.05% 아래, sell 은 0.05% 위 — 어느 쪽도 즉시 taker 가
    되지 않게 한다. tick-size 정렬은 broker adapter(``_quantize_price_to_tick``)
    가 담당하므로 여기서는 raw Decimal 만 반환한다.

    ``Decimal(str(...))`` 로 float 오염을 피한다 (conversion.py 와 동일 규약).
    """
    ref = Decimal(str(ref_price))
    if side == "buy":
        return ref * (Decimal("1") - _POST_ONLY_OFFSET)
    return ref * (Decimal("1") + _POST_ONLY_OFFSET)


def _build_order_request(intent: OrderIntent, idempotency_key: str) -> OrderRequest:
    """OrderIntent → OrderRequest. post-only 진입이면 GTX LIMIT 으로 변환.

    ``intent.entry_order_type == "post_only"`` 이고 ``ref_price`` 가 있으면
    maker 보장 limit 가격을 산출해 ``order_type=LIMIT, tif=GTX`` 로 변환한다.
    그 외(기본값 "market", 또는 ref_price 누락 = orchestrator 산출 실패)는
    기존 MARKET 변환 — legacy 경로 byte-identical.

    ValueError(미등록 심볼 / LIMIT price 누락)는 caller(execute_intents)가
    잡아 REJECTED ack 으로 강등한다.
    """
    if intent.entry_order_type == "post_only" and intent.ref_price is not None:
        limit_price = _post_only_limit_price(intent.side, intent.ref_price)
        return intent_to_order_request(
            intent,
            idempotency_key=idempotency_key,
            order_type=OrderType.LIMIT,
            price=limit_price,
            tif=TimeInForce.GTX,
        )
    return intent_to_order_request(intent, idempotency_key=idempotency_key)
