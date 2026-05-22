"""post-only Maker 진입 미체결 fallback (post-only-maker-entry.draft.md, 3단계).

GTX(post-only) LIMIT 주문은 즉시 체결되지 않는다 — 발주 직후 두 갈래:

  - **EXPIRED** — taker 가 될 주문이라 거래소가 placement 시점에 거부.
    → 즉시 전량 시장가 재발주 (대기 없음).
  - **NEW** — 호가창에 maker 로 안착. → ``POST_ONLY_FALLBACK_SEC`` 대기 후
    미체결분을 cancel → 잔량(origQty - executedQty)만 시장가 재발주.

핵심 안전장치:

  - **cancel-race (gap D)** — cancel 직전/도중 maker 로 체결될 수 있다.
    cancel 실패 시 re-GET 으로 종결 상태를 확인하고, FILLED 면 재발주하지
    않는다 (중복 포지션 차단). cancel 이 실패했는데 주문이 아직 살아있으면
    (NEW/PARTIALLY_FILLED) 보수적으로 재발주를 포기한다.
  - **partial fill** — GTX 도 호가창에서 일부만 maker 체결되고 나머지가
    NEW 로 남을 수 있다. cancel 후 ``executedQty`` 를 읽어 잔량만 재발주.
  - **리스크 우회 금지** — 시장가 재발주는 :func:`execute_intents` 를 그대로
    재사용한다 → KillSwitch / sizing / WAL / position_store 동일 파이프라인.
  - **_live_entered 박제 회피 (4단계)** — 완전 미체결(체결 0) + 시장가
    재발주도 REJECTED 면 ``on_entry_unfilled`` 콜백으로
    ``orchestrator.sync_live_entered(sid, sym, 0)`` 을 호출해 진입 기록을
    해제한다. 미해제 시 그 (sid, symbol) 은 프로세스 수명 동안 영구
    진입차단 (PR #287 가 고친 버그의 재현).

GTX maker 체결 자체는 Binance user-data WS stream → ``run_binance_fill_consumer``
→ ``order_filled`` WAL 로 이미 처리된다. 본 모듈은 *미체결* 케이스만 다룬다.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from src.brokers.base import OrderAck, OrderRequest
from src.execution.base import MarketState
from src.live.types import EVENT_POST_ONLY_FALLBACK, WALEvent
from src.live.wal import WAL
from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch
from src.portfolio.order_intent import OrderIntent

logger = logging.getLogger(__name__)

# post-only NEW 주문을 미체결로 판정하기까지의 대기 시간(초). mean-reversion
# 진입은 지지선 근처 횡보라 이 안에 maker 체결될 확률이 높다. breakout 으로
# 확대 시(5단계) 미체결률을 보고 조정. 호출부에서 override 가능.
POST_ONLY_FALLBACK_SEC: float = 30.0

# 종료 시 취소해야 할 in-flight fallback task. asyncio 는 create_task 결과에
# strong ref 를 유지하지 않으므로(GC 위험) 여기에 담아둔다. task 는 완료 시
# add_done_callback 으로 스스로 discard.
_BACKGROUND_TASKS: set[asyncio.Task] = set()

# fallback task 가 도달 가능한 종결(terminal) 주문 상태.
_TERMINAL_STATES = ("FILLED", "EXPIRED", "CANCELED", "CANCELLED", "REJECTED")

# release 콜백 시그니처: (strategy_id, symbol) -> None
ReleaseCallback = Callable[[str, str], None]


def is_post_only(req: OrderRequest) -> bool:
    """``req`` 가 post-only(GTX LIMIT) 주문인지."""
    from src.brokers.base import OrderType
    from src.execution.base import TimeInForce

    return req.order_type == OrderType.LIMIT and req.tif == TimeInForce.GTX


def _emit_fallback_event(
    wal: WAL,
    intent: OrderIntent,
    outcome: str,
    filled_qty: Decimal,
    remaining_qty: Decimal,
) -> None:
    """post-only fallback 결과를 WAL 에 기록 — 미체결률 모니터링용.

    outcome ∈ {filled_maker, filled_during_cancel, resubmitted_market,
    total_miss, cancel_failed_abort}.
    """
    try:
        wal.write(WALEvent(
            ts=datetime.now(timezone.utc).isoformat(),
            event_type=EVENT_POST_ONLY_FALLBACK,
            payload={
                "strategy_id": intent.strategy_id,
                "symbol": intent.symbol,
                "outcome": outcome,
                "filled_qty": str(filled_qty),
                "remaining_qty": str(remaining_qty),
            },
        ))
    except Exception as exc:  # noqa: BLE001 — WAL 실패가 주문 경로를 깨면 안 됨
        logger.warning("post_only_fallback WAL write failed: %s", exc)


async def _safe_get_order(broker, symbol: str, coid: str) -> OrderAck | None:
    """주문 상태 조회. 실패 시 None (caller 가 보수적으로 abort)."""
    try:
        return await broker.get_order(symbol=symbol, client_order_id=coid)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "post_only_fallback get_order failed coid=%s: %s", coid, exc,
        )
        return None


async def _safe_cancel(broker, symbol: str, coid: str) -> bool:
    """주문 취소. 성공 True, 실패 False.

    이미 체결/소멸한 주문은 거래소가 cancel 에 에러를 돌려주는데(정상),
    이 경우 caller 는 re-GET 으로 종결 상태를 확인한다 (cancel-race 처리).
    """
    try:
        await broker.cancel_order(symbol=symbol, client_order_id=coid)
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "post_only_fallback cancel raised coid=%s: %s "
            "(order likely already terminal — re-GET will confirm)",
            coid, exc,
        )
        return False


async def resubmit_post_only_as_market(
    intent: OrderIntent,
    *,
    qty: Decimal,
    already_filled: Decimal,
    broker,
    kill_switch: KillSwitch,
    wal: WAL,
    metrics: Metrics,
    market_state: MarketState | None,
    position_store=None,
    on_entry_unfilled: ReleaseCallback | None = None,
) -> list[OrderAck]:
    """미체결 잔량 ``qty`` 를 시장가로 재발주.

    :func:`execute_intents` 를 그대로 재사용한다 — KillSwitch / sizing / WAL /
    position_store 동일 파이프라인 (리스크 우회 금지, draft "리스크 연동").
    재발주 intent 는 ``entry_order_type="market"`` 이라 재귀 fallback 없음.

    완전 미체결(``already_filled == 0``) + 시장가 재발주도 REJECTED 면
    ``on_entry_unfilled`` 콜백으로 ``_live_entered`` 를 해제한다 (4단계).
    부분 체결분이 있으면(``already_filled > 0``) 포지션이 존재하므로 해제하지
    않는다 — 잔량 재발주가 dust 라 거부돼도 마찬가지.
    """
    # circular import 회피 — executor 가 본 모듈을 import 한다.
    from src.live.executor import execute_intents

    if qty <= 0:
        # cancel 후 잔량 0 = 사실상 전량 maker 체결. 재발주 불필요.
        _emit_fallback_event(wal, intent, "filled_maker", already_filled, Decimal("0"))
        return []

    market_intent = OrderIntent(
        strategy_id=intent.strategy_id,
        symbol=intent.symbol,
        side=intent.side,
        qty=float(qty),
        reason=f"{intent.reason}|post_only_fallback",
        reduce_only=intent.reduce_only,
        entry_order_type="market",  # 재귀 fallback 차단
    )
    acks = await execute_intents(
        [market_intent],
        broker=broker,
        kill_switch=kill_switch,
        wal=wal,
        metrics=metrics,
        market_state=market_state,
        position_store=position_store,
    )

    market_rejected = (not acks) or acks[0].status == "REJECTED"
    if market_rejected and already_filled == 0:
        # 완전 미체결 + 시장가도 거부 → 포지션 0 → _live_entered 해제.
        _emit_fallback_event(wal, intent, "total_miss", Decimal("0"), qty)
        if on_entry_unfilled is not None:
            try:
                on_entry_unfilled(intent.strategy_id, intent.symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "on_entry_unfilled callback failed sid=%s sym=%s: %s",
                    intent.strategy_id, intent.symbol, exc,
                )
    else:
        _emit_fallback_event(
            wal, intent, "resubmitted_market", already_filled, qty,
        )
    return acks


async def run_post_only_fallback(
    intent: OrderIntent,
    req: OrderRequest,
    *,
    broker,
    kill_switch: KillSwitch,
    wal: WAL,
    metrics: Metrics,
    market_state: MarketState | None,
    position_store=None,
    on_entry_unfilled: ReleaseCallback | None = None,
    fallback_sec: float = POST_ONLY_FALLBACK_SEC,
) -> None:
    """post-only NEW 주문을 ``fallback_sec`` 대기 후 미체결분 시장가 재발주.

    :func:`schedule_post_only_fallback` 가 background task 로 띄운다. 절대
    호출 loop(consumer tick)를 블록하지 않는다.
    """
    symbol = intent.symbol
    coid = req.client_order_id

    try:
        await asyncio.sleep(fallback_sec)

        cur = await _safe_get_order(broker, symbol, coid)
        if cur is None:
            # 상태 조회 불가 → 재발주 시 중복 위험 → 보수적으로 포기.
            # 주문은 호가창에 남아있을 수 있고, 그러면 maker 로 체결되거나
            # PositionReconciler 가 추후 정합한다.
            logger.warning(
                "post_only_fallback: get_order unavailable coid=%s — abort", coid,
            )
            return

        # orig = 거래소가 실제 받은 주문 수량(origQty). adapter 의 lot-floor
        # 로 req.qty 보다 작을 수 있어 get_order 결과를 권위값으로 쓴다.
        orig = cur.qty if cur.qty is not None else req.qty

        if cur.status == "FILLED":
            # maker 로 전량 체결됨 — order_filled 는 WS consumer 가 기록.
            _emit_fallback_event(
                wal, intent, "filled_maker", cur.filled_qty or orig, Decimal("0"),
            )
            return

        if cur.status in ("EXPIRED", "CANCELED", "CANCELLED", "REJECTED"):
            # NEW 였다가 종결됨(드묾 — 거래소측 만료/취소). 미체결분 시장가.
            filled = cur.filled_qty or Decimal("0")
            await resubmit_post_only_as_market(
                intent, qty=orig - filled, already_filled=filled,
                broker=broker, kill_switch=kill_switch, wal=wal, metrics=metrics,
                market_state=market_state, position_store=position_store,
                on_entry_unfilled=on_entry_unfilled,
            )
            return

        # status ∈ {NEW, PARTIALLY_FILLED} → 아직 호가창 활성 → cancel 필요.
        cancelled = await _safe_cancel(broker, symbol, coid)

        # cancel 후 종결 상태 재조회 — executedQty 를 확정한다. cancel 과
        # get_order 사이에도 체결될 수 있으므로 이 재조회 결과가 권위 있다.
        final = await _safe_get_order(broker, symbol, coid)
        if final is not None:
            filled = final.filled_qty if final.filled_qty is not None else (
                cur.filled_qty or Decimal("0")
            )
            status = final.status
        else:
            filled = cur.filled_qty or Decimal("0")
            status = cur.status

        if status == "FILLED":
            # cancel-race (gap D): cancel 직전/도중 전량 maker 체결됨
            # → 재발주하면 중복 포지션 → 재발주 금지.
            _emit_fallback_event(
                wal, intent, "filled_during_cancel", filled, Decimal("0"),
            )
            return

        if not cancelled and status not in _TERMINAL_STATES:
            # cancel 실패 + 주문이 아직 살아있음(NEW/PARTIALLY_FILLED)
            # → 지금 시장가를 쏘면 그 주문이 나중에 체결될 때 중복.
            # → 보수적으로 포기. _live_entered 는 그대로 두고(주문이 추후
            #   체결될 수 있음) PositionReconciler 의 정합에 맡긴다.
            logger.error(
                "post_only_fallback: cancel failed & order still live "
                "coid=%s status=%s — NOT resubmitting (dup-order guard)",
                coid, status,
            )
            _emit_fallback_event(
                wal, intent, "cancel_failed_abort", filled, orig - filled,
            )
            return

        # 정상 경로: 주문이 종결됐고(cancel 성공 또는 이미 terminal),
        # 미체결 잔량을 시장가로 재발주.
        await resubmit_post_only_as_market(
            intent, qty=orig - filled, already_filled=filled,
            broker=broker, kill_switch=kill_switch, wal=wal, metrics=metrics,
            market_state=market_state, position_store=position_store,
            on_entry_unfilled=on_entry_unfilled,
        )

    except asyncio.CancelledError:
        # 종료 중 취소 — 조용히 전파 (종료 후 시장가 발사 방지).
        raise
    except Exception as exc:  # noqa: BLE001 — fallback task 가 죽어도 loop 보호
        logger.error(
            "post_only_fallback unexpected error coid=%s: %s", coid, exc,
        )


def schedule_post_only_fallback(
    intent: OrderIntent,
    req: OrderRequest,
    *,
    broker,
    kill_switch: KillSwitch,
    wal: WAL,
    metrics: Metrics,
    market_state: MarketState | None,
    position_store=None,
    on_entry_unfilled: ReleaseCallback | None = None,
    fallback_sec: float | None = None,
) -> asyncio.Task:
    """post-only NEW 주문의 fallback 을 background task 로 예약한다.

    consumer tick loop 을 블록하지 않도록 :func:`run_post_only_fallback` 을
    별도 task 로 띄운다. task 는 ``_BACKGROUND_TASKS`` 에 등록되고 완료 시
    스스로 빠진다 (GC + 종료 시 취소 대상).
    """
    task = asyncio.create_task(
        run_post_only_fallback(
            intent, req,
            broker=broker, kill_switch=kill_switch, wal=wal, metrics=metrics,
            market_state=market_state, position_store=position_store,
            on_entry_unfilled=on_entry_unfilled,
            fallback_sec=(
                fallback_sec if fallback_sec is not None else POST_ONLY_FALLBACK_SEC
            ),
        ),
        name=f"post-only-fallback-{intent.symbol}",
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


async def cancel_pending_fallbacks() -> None:
    """in-flight post-only fallback task 를 모두 취소하고 종료를 기다린다.

    ``run_shadow_loop`` 종료 시 호출 — 미취소 시 데몬 종료 후에도 대기
    중이던 task 가 깨어나 시장가를 발사할 수 있다.
    """
    tasks = list(_BACKGROUND_TASKS)
    for t in tasks:
        if not t.done():
            t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
