"""Binance user-data fill stream → ``order_filled`` WAL consumer.

Root incident
-------------
``src/brokers/binance/async_ws.py::stream_fills()`` (yields ``BrokerFill``)
has NO production caller — only tests. On the live ``binance-testnet-shadow``
path the executor only writes ``order_acked`` (intent: a Binance MARKET ack
is status=NEW with no price). No ``order_filled`` WAL event is ever emitted,
so ``PnLAggregator`` / ``StrategyPositionStore`` / ``trade_history`` (all of
which early-return on ``event_type != "order_filled"``) show ZERO realized
P&L / no positions / no trades for the entire Binance live path. The
dashboard shows the SUBMITTED INTENT forever, never the actual fill.

This module is the production consumer that closes the gap.

Design
------
* ``broker_fill_to_order_filled_event`` — pure mapping of a ``BrokerFill``
  to a ``WALEvent(event_type="order_filled")`` whose payload is
  byte-compatible with the schema ``PaperBroker`` already emits (so
  ``PnLAggregator.ingest_fill_event`` / ``StrategyPositionStore`` /
  ``trade_history.reconstruct_trades`` read it with zero changes).
* ``run_binance_fill_consumer`` — a bounded-reconnect async task that
  iterates ``stream_fills()``, resolves symbol/side/strategy_id from the
  ``StrategyPositionStore`` coid→context map (registered by the executor
  BEFORE place_order), de-dupes on the Binance ``(broker_order_id,
  trade_id)`` key, and writes the event through the *existing* ``WAL``.
  The WAL's ``observer`` (wired in ``scripts/live_run.py``) then fans the
  event out to timeline + position store + pnl aggregator — i.e. the SAME
  established seam the paper path uses for ``order_filled``. No parallel
  path is built.

A real-money fill whose coid can't be resolved is STILL written to the WAL
(``strategy_id`` key absent) plus a WARNING — never silently lost, so it is
durable for forensics/audit. NOTE: such a fill is currently NOT counted by
``PnLAggregator`` / ``StrategyPositionStore`` / ``trade_history`` — all three
require a resolvable ``strategy_id`` and skip the event otherwise (it does
NOT keep venue totals correct). This only occurs in the narrow window of a
daemon restart with an order in flight (the in-memory order-context map is
lost and the ``order_filled`` WAL row was not yet written); the normal
in-process path always resolves the coid. Tracked follow-up: emit a
``__unattributed__`` sentinel (or venue-level bucket) so totals stay exact.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from src.brokers.errors import WSConfigError
from src.brokers.types import BrokerFill
from src.live.reconnect import backoff_delay
from src.live.types import WALEvent
from src.live.wal import WAL, WALWriteFailed

logger = logging.getLogger(__name__)

# Bounded reconnect ceiling — a permanently-down user-data WS eventually
# gives up rather than spinning forever, but the daemon outlives any
# transient outage (mirrors loop.py producer intent).
_DEFAULT_MAX_ATTEMPTS = 100


def _resolve_fill_attribution(ctx, fill, position_store):
    """(symbol, side, strategy_id) 결정 — coid context 우선, 없으면 fill 자체.

    coid 가 in-memory order-context 맵에 있으면 그대로(진입 경로). 없으면(거래소
    네이티브 TP/SL 청산 등 우리가 등록 안 한 plan order) **fill 자체의 symbol/side**
    (Bitget orders 채널 instId/side)를 쓰고, strategy_id 는 그 symbol 의 *단독
    보유* 전략에 귀속한다 (``StrategyPositionStore.sole_holder_strategy``):
      - 1명: 귀속 → 청산이 store 에서 정상 차감 (인플레이션 근본 차단).
      - 0명(수동/외부, 예: ORDI) / 2명(다전략) → None → 미귀속(현행 유지).

    fill 에 symbol 이 없으면(예: binance BrokerFill, 기본 "") symbol="" →
    strategy_id None → 기존 동작 byte-identical.
    """
    if ctx is not None:
        return ctx  # (symbol, side, strategy_id)
    symbol = getattr(fill, "symbol", "") or ""
    side = getattr(fill, "side", "") or ""
    strategy_id = (
        position_store.sole_holder_strategy(symbol)
        if (symbol and position_store is not None) else None
    )
    return symbol, side, strategy_id


def broker_fill_to_order_filled_event(
    fill: BrokerFill,
    *,
    symbol: str,
    side: str,
    strategy_id: str | None,
) -> WALEvent:
    """Map a ``BrokerFill`` to an ``order_filled`` ``WALEvent``.

    The payload mirrors ``PaperBroker``'s ``order_filled`` schema exactly
    (``symbol``, ``side``, ``qty``, ``fill_qty``, ``fill_price``, ``fees``,
    ``fee_asset``, ``client_order_id``, ``broker_order_id``, ``trade_id``,
    ``server_ts``) so every existing replay/ingest consumer reads it with no
    changes. ``ts`` is the broker fill timestamp (drives the KST-09:00
    business-window classification + cross-run trade ordering).

    ``strategy_id`` is added ONLY when resolvable — an absent key (rather
    than ``None``) keeps the payload byte-identical to a legacy/no-strategy
    fill so the WAL never carries a misleading null and downstream
    ``payload.get("strategy_id")`` semantics are unchanged.
    """
    payload: dict = {
        "client_order_id": fill.client_order_id,
        "broker_order_id": fill.broker_order_id,
        "symbol": symbol,
        "side": side,
        # ACTUAL fill quantities — never the submitted intent qty.
        "qty": str(fill.qty),
        "fill_qty": str(fill.qty),
        "fill_price": str(fill.price),
        "fees": str(fill.fee),
        "fee_asset": fill.fee_asset,
        "trade_id": fill.trade_id,
        "server_ts": None,
        # Persisted broker fill timestamp — read by PnLAggregator
        # (business-window) and trade_history (deterministic ordering).
        "ts": fill.ts.isoformat(),
    }
    if strategy_id is not None:
        payload["strategy_id"] = strategy_id
    return WALEvent(
        ts=fill.ts.isoformat(),
        event_type="order_filled",
        payload=payload,
    )


async def run_binance_fill_consumer(
    stream_factory: Callable[[], AsyncIterator[BrokerFill]],
    *,
    wal: WAL,
    position_store,
    stop_event: asyncio.Event,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Consume the Binance user-data fill stream into ``order_filled`` WAL.

    ``stream_factory`` returns a fresh ``AsyncIterator[BrokerFill]`` each
    call (a new ``stream_fills()``); it is re-invoked on reconnect so a
    dropped WS gets a fresh listenKey/connection. The consumer:

    * de-dupes on the Binance ``(broker_order_id, trade_id)`` pair — a
      reconnect rebuilds the per-stream dedup set in ``async_ws``, so
      cross-reconnect resends are caught HERE;
    * resolves ``(symbol, side, strategy_id)`` from the position store's
      coid→context map; an unresolved coid still emits the fill (warned,
      strategy_id absent) — a real fill is never dropped;
    * writes through the supplied ``WAL`` whose ``observer`` fan-out reaches
      timeline + StrategyPositionStore + PnLAggregator (the established
      seam — no parallel path);
    * never crashes the caller: any stream error backs off
      (``reconnect.backoff_delay``) and resumes, bounded by
      ``max_attempts``; ``CancelledError`` propagates cleanly for a tidy
      shutdown.
    """
    seen: set[tuple[str, str]] = set()
    attempt = 0

    while not stop_event.is_set() and attempt < max_attempts:
        try:
            stream = stream_factory()
            async for fill in stream:
                if stop_event.is_set():
                    break
                attempt = 0  # reset on any successful delivery
                dedup_key = (fill.broker_order_id, fill.trade_id)
                if dedup_key in seen:
                    logger.debug(
                        "binance_fill_consumer: duplicate fill skipped %s",
                        dedup_key,
                    )
                    continue
                seen.add(dedup_key)

                ctx = position_store.resolve_order_context(
                    fill.client_order_id
                ) if position_store is not None else None
                symbol, side, strategy_id = _resolve_fill_attribution(
                    ctx, fill, position_store,
                )
                if strategy_id is None:
                    # Unresolvable coid + no sole-holder — emit anyway (real
                    # money), warn. totals stay correct; per-strategy unattributed.
                    logger.warning(
                        "binance_fill_consumer: cannot resolve order context "
                        "for coid=%r (broker_order_id=%s trade_id=%s symbol=%r) "
                        "— emitting order_filled WITHOUT strategy_id",
                        fill.client_order_id, fill.broker_order_id,
                        fill.trade_id, symbol,
                    )

                event = broker_fill_to_order_filled_event(
                    fill, symbol=symbol, side=side, strategy_id=strategy_id,
                )
                try:
                    wal.write(event)
                except WALWriteFailed as exc:
                    # Do NOT crash the trading loop on a transient WAL error;
                    # the fill is logged so it is not silently lost.
                    logger.error(
                        "binance_fill_consumer: WAL write failed for fill "
                        "coid=%r trade_id=%s: %s",
                        fill.client_order_id, fill.trade_id, exc,
                    )
            # Stream completed cleanly (e.g. aclose / iterator exhausted).
            return
        except asyncio.CancelledError:
            raise
        except WSConfigError as err:
            # PERMANENT misconfig (wrong ws_base_url / 4xx handshake). Retrying
            # cannot succeed — surface ONCE with remediation and stop the
            # consumer (the trading loop is unaffected: it spawns this as a
            # separate task; live fills simply won't reach the WAL until the
            # operator fixes BINANCE_WS_BASE_URL). Do NOT reconnect-storm.
            logger.error(
                "binance_fill_consumer: fill stream permanently unavailable "
                "(%s) — NOT retrying. Live Binance fills will NOT reach the "
                "dashboard/PnL until BINANCE_WS_BASE_URL is corrected "
                "(testnet: wss://stream.binancefuture.com/ws). Trading "
                "continues; this only affects fill visibility.",
                err,
            )
            return
        except BaseException as err:  # noqa: BLE001 — never crash the loop
            if stop_event.is_set():
                return
            attempt += 1
            if attempt >= max_attempts:
                logger.error(
                    "binance_fill_consumer: reconnect exhausted %d attempts "
                    "(%s: %s) — giving up; live fills will no longer reach "
                    "the WAL until restart",
                    max_attempts, type(err).__name__, err,
                )
                return
            delay = backoff_delay(attempt - 1, base=1.0, cap=30.0)
            logger.warning(
                "binance_fill_consumer: stream error (attempt=%d/%d, "
                "sleep=%.1fs): %s: %s",
                attempt, max_attempts, delay,
                type(err).__name__, err,
            )
            if sleep is asyncio.sleep:
                # Production: stay responsive to a shutdown during backoff.
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=delay)
                    return  # stop requested during backoff
                except asyncio.TimeoutError:
                    pass
                except asyncio.CancelledError:
                    raise
            else:
                # Injected sleep hook (tests) — deterministic, fast.
                await sleep(delay)
                if stop_event.is_set():
                    return


async def run_bitget_fill_consumer(
    stream_factory: Callable[[], AsyncIterator[BrokerFill]],
    *,
    wal: WAL,
    position_store,
    stop_event: asyncio.Event,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_fill: Callable[..., Awaitable[None]] | None = None,
) -> None:
    """Bitget v2 private WS user-data fill consumer (P4b).

    ``on_fill`` (옵션) — 매 체결 WAL 기록 후 await 호출되는 콜백
    ``(symbol, side, strategy_id, fill)``. 거래소 네이티브 TP/SL 코디네이터가
    진입 시 보호주문 등록 / 청산 시 취소하는 데 쓴다. store 가 WAL fanout 으로
    갱신된 *후* 호출되므로 콜백이 net 을 정확히 읽는다. 콜백 예외는 삼켜
    fill 처리를 깨지 않는다.

    Same contract as :func:`run_binance_fill_consumer` — only the logger
    labels and the WSConfigError remediation message differ (env-vars are
    BITGET_DEMO_* / BITGET_API_*; subdomain is wspap.bitget.com for demo).
    """
    seen: set[tuple[str, str]] = set()
    attempt = 0

    while not stop_event.is_set() and attempt < max_attempts:
        try:
            stream = stream_factory()
            async for fill in stream:
                if stop_event.is_set():
                    break
                attempt = 0
                dedup_key = (fill.broker_order_id, fill.trade_id)
                if dedup_key in seen:
                    logger.debug(
                        "bitget_fill_consumer: duplicate fill skipped %s",
                        dedup_key,
                    )
                    continue
                seen.add(dedup_key)

                ctx = position_store.resolve_order_context(
                    fill.client_order_id
                ) if position_store is not None else None
                symbol, side, strategy_id = _resolve_fill_attribution(
                    ctx, fill, position_store,
                )
                if strategy_id is None:
                    logger.warning(
                        "bitget_fill_consumer: cannot resolve order context "
                        "for coid=%r (broker_order_id=%s trade_id=%s symbol=%r) "
                        "— emitting order_filled WITHOUT strategy_id",
                        fill.client_order_id, fill.broker_order_id,
                        fill.trade_id, symbol,
                    )

                event = broker_fill_to_order_filled_event(
                    fill, symbol=symbol, side=side, strategy_id=strategy_id,
                )
                try:
                    wal.write(event)
                except WALWriteFailed as exc:
                    logger.error(
                        "bitget_fill_consumer: WAL write failed for fill "
                        "coid=%r trade_id=%s: %s",
                        fill.client_order_id, fill.trade_id, exc,
                    )
                # 거래소 네이티브 TP/SL 코디네이터 hook — store 갱신 후 호출.
                if on_fill is not None:
                    try:
                        await on_fill(
                            symbol=symbol, side=side,
                            strategy_id=strategy_id, fill=fill,
                        )
                    except Exception as exc:  # noqa: BLE001 — fill 경로 보호
                        logger.error(
                            "bitget_fill_consumer: on_fill(protective) error "
                            "coid=%r: %s", fill.client_order_id, exc,
                        )
            return
        except asyncio.CancelledError:
            raise
        except WSConfigError as err:
            logger.error(
                "bitget_fill_consumer: fill stream permanently unavailable "
                "(%s) — NOT retrying. Live Bitget fills will NOT reach the "
                "dashboard/PnL until BITGET_DEMO_API_KEY / BITGET_DEMO_SECRET "
                "/ BITGET_DEMO_PASSPHRASE are corrected (demo subdomain "
                "is wspap.bitget.com). Trading continues; this only affects "
                "fill visibility.",
                err,
            )
            return
        except BaseException as err:  # noqa: BLE001
            if stop_event.is_set():
                return
            attempt += 1
            if attempt >= max_attempts:
                logger.error(
                    "bitget_fill_consumer: reconnect exhausted %d attempts "
                    "(%s: %s) — giving up; live fills will no longer reach "
                    "the WAL until restart",
                    max_attempts, type(err).__name__, err,
                )
                return
            delay = backoff_delay(attempt - 1, base=1.0, cap=30.0)
            logger.warning(
                "bitget_fill_consumer: stream error (attempt=%d/%d, "
                "sleep=%.1fs): %s: %s",
                attempt, max_attempts, delay,
                type(err).__name__, err,
            )
            if sleep is asyncio.sleep:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=delay)
                    return
                except asyncio.TimeoutError:
                    pass
                except asyncio.CancelledError:
                    raise
            else:
                await sleep(delay)
                if stop_event.is_set():
                    return
