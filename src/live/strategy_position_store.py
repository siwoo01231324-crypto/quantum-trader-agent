"""In-memory per-strategy position tracker, fed by broker fill events (#192).

Backs `DashboardState.position_provider` so that the strategy ON/OFF toggle
(`POST /api/strategies/{id}/toggle`) can liquidate only that strategy's
holdings without touching positions opened by other strategies.

Two ingestion paths:
  - direct: `record_fill(strategy_id, symbol, side, qty)`
  - by client_order_id: `register_order(coid, strategy_id)` at place-order
    time, then `record_fill_by_client_order_id(coid, ...)` when the fill
    arrives. Falls back to parsing the strategy_id prefix from coid when
    the registration was missed (e.g. WAL replay of legacy entries).

WAL replay (`replay_from_wal`) reconstructs state on boot using
`order_filled` events. New payloads carry `strategy_id` directly (#192);
legacy payloads fall back to the `{strategy_id}:{symbol}:{ts}:{idx}`
prefix in `client_order_id`.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

from src.live.wal import replay

logger = logging.getLogger(__name__)


class StrategyPositionStore:
    def __init__(self) -> None:
        self._positions: dict[str, dict[str, Decimal]] = {}
        self._order_strategy: dict[str, str] = {}
        # coid → (symbol, side, strategy_id). Populated alongside
        # register_order by the executor before broker.place_order(). The
        # Binance fill stream (async_ws.BrokerFill) carries the coid but NOT
        # symbol/side, so the live fill consumer resolves the full context
        # from here to build a complete order_filled WAL payload.
        self._order_context: dict[str, tuple[str, str, str]] = {}

    def register_order(self, *, client_order_id: str, strategy_id: str) -> None:
        """Remember which strategy placed a given client_order_id.

        Called by executor before broker.place_order(). Lets us tag fills
        even when the broker payload doesn't echo strategy_id back.
        """
        self._order_strategy[client_order_id] = strategy_id

    def register_order_context(
        self,
        *,
        client_order_id: str,
        symbol: str,
        side: str,
        strategy_id: str,
    ) -> None:
        """Remember the full order context for a client_order_id.

        Additive companion to ``register_order`` (whose signature is
        deliberately left unchanged — the executor coid-attribution spy
        subclass depends on it). The Binance user-data ``BrokerFill`` only
        carries the coid + qty/price/fee, so the live fill consumer needs
        symbol + side from here to assemble a PaperBroker-schema-compatible
        ``order_filled`` payload.
        """
        self._order_context[client_order_id] = (symbol, side, strategy_id)

    def resolve_order_context(
        self, client_order_id: str
    ) -> tuple[str, str, str] | None:
        """(symbol, side, strategy_id) for a coid, or None if not registered.

        Cross-run note: this in-memory map (like ``_order_strategy``) is lost
        on restart; the persisted ``strategy_id`` in the WAL ``order_filled``
        payload is what makes cross-run trade_history correct.
        """
        return self._order_context.get(client_order_id)

    def record_fill(
        self,
        *,
        strategy_id: str,
        symbol: str,
        side: str,
        qty: Decimal,
    ) -> None:
        delta = qty if side.lower() == "buy" else -qty
        bucket = self._positions.setdefault(strategy_id, {})
        bucket[symbol] = bucket.get(symbol, Decimal("0")) + delta

    def record_fill_by_client_order_id(
        self,
        *,
        client_order_id: str,
        symbol: str,
        side: str,
        qty: Decimal,
    ) -> None:
        strategy_id = self._resolve_strategy(client_order_id)
        if strategy_id is None:
            logger.warning(
                "record_fill_by_client_order_id: cannot resolve strategy_id from %s",
                client_order_id,
            )
            return
        self.record_fill(strategy_id=strategy_id, symbol=symbol, side=side, qty=qty)

    def get_positions(self, strategy_id: str) -> list[tuple[str, float]]:
        bucket = self._positions.get(strategy_id, {})
        return [
            (symbol, float(qty))
            for symbol, qty in sorted(bucket.items())
            if qty != 0
        ]

    def all_positions(self) -> dict[str, list[tuple[str, float]]]:
        """All non-zero positions across all strategies — used at startup to
        restore orchestrator._live_entered (preventing re-entry on restart).

        Returns: {strategy_id: [(symbol, qty), ...]} for every strategy with at
        least one non-zero position. Empty dict if no positions tracked.
        """
        out: dict[str, list[tuple[str, float]]] = {}
        for sid in self._positions:
            positions = self.get_positions(sid)
            if positions:
                out[sid] = positions
        return out

    def replay_from_wal(self, wal_path: Path | str) -> None:
        """Cross-run restore from WAL.

        Processes 2 event types:
        1. ``order_acked`` / ``order_placed`` — restore in-memory register_order
           + register_order_context maps (coid → strategy_id, symbol, side).
           Without this, **재시작 후 발생한 sell fill** 이 in-memory map 비어있어
           strategy_id 결정 실패 → WAL payload 에 strategy_id 누락 → ingest_fill_event
           가 drop → store/aggregator 갱신 안 됨. 이슈 4/5 의 진짜 long-term 원인.
        2. ``order_filled`` — replay fill events 로 logical position 복원.

        Event order in WAL is chronological so acks/placed come before fills,
        ensuring the map is warm by the time a fill is ingested.
        """
        events, _corruptions = replay(wal_path)
        for event in events:
            et = event.event_type
            payload = event.payload or {}
            if et in ("order_acked", "order_placed"):
                sid = payload.get("strategy_id")
                coid = payload.get("client_order_id")
                sym = payload.get("symbol")
                side = payload.get("side")
                if sid and coid:
                    self.register_order(client_order_id=coid, strategy_id=sid)
                    if sym and side:
                        self.register_order_context(
                            client_order_id=coid, symbol=sym,
                            side=side, strategy_id=sid,
                        )
            elif et == "order_filled":
                self.ingest_fill_event(et, payload)

    def ingest_fill_event(self, event_type: str, payload: dict) -> None:
        """Apply an `order_filled` event payload to the in-memory position map.

        Safe to call on non-fill event_types (no-op). Used by the live
        WAL observer in scripts/live_run.py so every fill written to the
        WAL flows through the store.
        """
        if event_type != "order_filled":
            return
        symbol = payload.get("symbol")
        side = payload.get("side")
        raw_qty = payload.get("fill_qty") or payload.get("qty")
        if not (symbol and side and raw_qty is not None):
            return
        try:
            qty = Decimal(str(raw_qty))
        except Exception as err:
            logger.warning("ingest_fill_event: bad qty %r in payload: %s", raw_qty, err)
            return
        strategy_id = payload.get("strategy_id") or self._resolve_strategy(
            payload.get("client_order_id", "")
        )
        if not strategy_id:
            logger.warning(
                "ingest_fill_event: cannot resolve strategy_id (coid=%r)",
                payload.get("client_order_id"),
            )
            return
        self.record_fill(strategy_id=strategy_id, symbol=symbol, side=side, qty=qty)

    def _resolve_strategy(self, client_order_id: str) -> str | None:
        if not client_order_id:
            return None
        if client_order_id in self._order_strategy:
            return self._order_strategy[client_order_id]
        # Fallback: parse `{strategy_id}:{symbol}:{ts}:{idx}` prefix.
        head, sep, _ = client_order_id.partition(":")
        return head if sep else None
