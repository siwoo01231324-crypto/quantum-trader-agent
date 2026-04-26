from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import AsyncIterator

from src.brokers.base import (
    AsyncBrokerAdapter,
    Balance,
    HealthStatus,
    MarginType,
    OrderAck,
    OrderRequest,
    OrderType,
    Position,
    PositionSide,
)
from src.brokers.types import BrokerFill
from src.execution.base import MarketState
from src.execution.mock_matching import MockMatchingEngine
from src.live.types import OrderStatus, WALEvent
from src.live.wal import WAL, WALWriteFailed, replay
from src.ops.kill_switch import KillSwitch, KillSwitchTripped

logger = logging.getLogger(__name__)


class PaperBroker:
    """Phase 1 paper-trading broker implementing AsyncBrokerAdapter Protocol.

    Order flow: kill_switch gate → WAL submit → match → WAL fill → state update → fills queue.
    WAL write failure trips kill switch and returns REJECTED.
    """

    name: str = "paper"
    paper: bool = True

    def __init__(
        self,
        wal: WAL,
        kill_switch: KillSwitch,
        matching_engine: MockMatchingEngine | None = None,
        initial_balance: Decimal = Decimal("100000"),
        balance_asset: str = "USDT",
    ) -> None:
        self._wal = wal
        self._kill_switch = kill_switch
        self._engine = matching_engine or MockMatchingEngine()
        self._balance_asset = balance_asset

        self._balances: dict[str, Balance] = {
            balance_asset: Balance(asset=balance_asset, free=initial_balance, locked=Decimal("0"))
        }
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, OrderAck] = {}  # client_order_id → ack
        self._fills_queue: asyncio.Queue[BrokerFill] = asyncio.Queue()
        self._latest_market_state: MarketState | None = None
        self._closed = False

    # --- live loop hook ---

    def update_market(self, state: MarketState) -> None:
        """Called by the live loop each tick to update current market state."""
        self._latest_market_state = state

    # --- AsyncBrokerAdapter Protocol ---

    async def place_order(self, req: OrderRequest) -> OrderAck:
        ts = datetime.now(timezone.utc)

        # 1. Kill switch gate
        try:
            self._kill_switch.assert_allow_order(liquidation=req.emergency_exit)
        except KillSwitchTripped as exc:
            ack = OrderAck(
                broker_order_id="",
                client_order_id=req.client_order_id,
                symbol=req.symbol,
                status=OrderStatus.REJECTED.value,
                ts=ts,
                reject_reason="KILL_SWITCH",
            )
            self._orders[req.client_order_id] = ack
            logger.warning("order blocked by kill switch: %s", exc)
            return ack

        # 2. WAL submit
        submitted_payload = {
            "client_order_id": req.client_order_id,
            "symbol": req.symbol,
            "side": req.side.value,
            "qty": str(req.qty),
            "price_intent": str(req.price) if req.price is not None else None,
            "order_type": req.order_type.value,
            "server_ts": None,
            "strategy_id": None,
        }
        submitted_event = WALEvent(
            ts=ts.isoformat(),
            event_type="order_submitted",
            payload=submitted_payload,
        )
        try:
            self._wal.write(submitted_event)
        except WALWriteFailed as exc:
            self._kill_switch.trip(reason="WAL_WRITE_FAIL", source="paper_broker")
            ack = OrderAck(
                broker_order_id="",
                client_order_id=req.client_order_id,
                symbol=req.symbol,
                status=OrderStatus.REJECTED.value,
                ts=ts,
                reject_reason="WAL_WRITE_FAIL",
            )
            self._orders[req.client_order_id] = ack
            logger.error("WAL write failed, kill switch tripped: %s", exc)
            return ack

        # 3. Market state required for matching
        if self._latest_market_state is None:
            ack = OrderAck(
                broker_order_id="",
                client_order_id=req.client_order_id,
                symbol=req.symbol,
                status=OrderStatus.REJECTED.value,
                ts=ts,
                reject_reason="NO_MARKET_STATE",
            )
            self._orders[req.client_order_id] = ack
            self._wal.write(WALEvent(
                ts=datetime.now(timezone.utc).isoformat(),
                event_type="order_rejected",
                payload={
                    "client_order_id": req.client_order_id,
                    "symbol": req.symbol,
                    "reject_reason": "NO_MARKET_STATE",
                    "error_message": "update_market() not called before place_order()",
                },
            ))
            return ack

        # 4. Match
        fills = self._engine.match(req, self._latest_market_state)

        if not fills:
            # Limit order price miss — REJECTED for Phase 1 (no resting orders)
            reject_ts = datetime.now(timezone.utc)
            self._wal.write(WALEvent(
                ts=reject_ts.isoformat(),
                event_type="order_rejected",
                payload={
                    "client_order_id": req.client_order_id,
                    "symbol": req.symbol,
                    "reject_reason": "LIMIT_PRICE_MISS",
                    "error_message": "limit order price not crossable at current market",
                },
            ))
            ack = OrderAck(
                broker_order_id="",
                client_order_id=req.client_order_id,
                symbol=req.symbol,
                status=OrderStatus.REJECTED.value,
                ts=reject_ts,
                reject_reason="LIMIT_PRICE_MISS",
            )
            self._orders[req.client_order_id] = ack
            return ack

        # 5. WAL fill + state update for each fill
        for fill in fills:
            fill_ts = datetime.now(timezone.utc)
            ack_latency_ms = (fill_ts - ts).total_seconds() * 1000

            filled_payload = {
                "client_order_id": fill.client_order_id,
                "broker_order_id": fill.broker_order_id,
                "symbol": req.symbol,
                "side": req.side.value,
                "qty": str(fill.qty),
                "fill_price": str(fill.price),
                "fill_qty": str(fill.qty),
                "fees": str(fill.fee),
                "fee_asset": fill.fee_asset,
                "ack_latency_ms": ack_latency_ms,
                "trade_id": fill.trade_id,
                "server_ts": None,
            }
            fill_event = WALEvent(
                ts=fill_ts.isoformat(),
                event_type="order_filled",
                payload=filled_payload,
            )
            try:
                self._wal.write(fill_event)
            except WALWriteFailed as exc:
                self._kill_switch.trip(reason="WAL_WRITE_FAIL", source="paper_broker")
                logger.error("WAL fill write failed, kill switch tripped: %s", exc)
                raise

            self._apply_fill(req, fill)
            await self._fills_queue.put(fill)

        last_fill = fills[-1]
        ack = OrderAck(
            broker_order_id=last_fill.broker_order_id,
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            status=OrderStatus.FILLED.value,
            ts=datetime.now(timezone.utc),
            qty=sum(f.qty for f in fills),
            price=last_fill.price,
        )
        self._orders[req.client_order_id] = ack
        return ack

    async def cancel_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> None:
        # Phase 1: no-op — resting orders not supported
        pass

    async def get_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> OrderAck:
        if client_order_id and client_order_id in self._orders:
            return self._orders[client_order_id]
        if broker_order_id:
            for ack in self._orders.values():
                if ack.broker_order_id == broker_order_id:
                    return ack
        raise KeyError(f"order not found: broker_order_id={broker_order_id} client_order_id={client_order_id}")

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        if symbol is not None:
            return [p for p in self._positions.values() if p.symbol == symbol]
        return list(self._positions.values())

    async def get_balance(self) -> list[Balance]:
        return list(self._balances.values())

    def stream_fills(self) -> AsyncIterator[BrokerFill]:
        return _FillStream(self._fills_queue)

    async def ensure_leverage(self, symbol: str, leverage: int) -> None:
        pass  # no-op for paper trading

    async def ensure_margin_type(self, symbol: str, mode: MarginType) -> None:
        pass  # no-op for paper trading

    async def ensure_position_mode(self, *, hedge: bool) -> None:
        pass  # no-op for paper trading

    async def health_check(self) -> HealthStatus:
        if self._kill_switch.tripped:
            return HealthStatus.DOWN
        return HealthStatus.OK

    async def aclose(self) -> None:
        self._closed = True

    # --- WAL replay ---

    @classmethod
    def from_wal(
        cls,
        path: Path | str,
        kill_switch: KillSwitch,
        matching_engine: MockMatchingEngine | None = None,
        initial_balance: Decimal = Decimal("100000"),
        balance_asset: str = "USDT",
    ) -> "PaperBroker":
        """Restore PaperBroker state by replaying a WAL file."""
        wal = WAL(path)
        broker = cls(
            wal=wal,
            kill_switch=kill_switch,
            matching_engine=matching_engine,
            initial_balance=initial_balance,
            balance_asset=balance_asset,
        )
        events, corruptions = replay(path)
        if corruptions:
            logger.warning("WAL replay: %d corrupted lines skipped", len(corruptions))
        for event in events:
            broker._apply_event(event)
        return broker

    # --- internal helpers ---

    def _apply_event(self, event: WALEvent) -> None:
        """Apply a WAL event to broker state. Only order_filled mutates position/balance."""
        if event.event_type != "order_filled":
            return
        p = event.payload
        try:
            from src.execution.base import Side
            side = Side(p["side"])
            fill_qty = Decimal(p["fill_qty"])
            fill_price = Decimal(p["fill_price"])
            fees = Decimal(p["fees"])
            symbol = p["symbol"]
            fee_asset = p.get("fee_asset", "USDT")
        except (KeyError, ValueError) as exc:
            logger.warning("_apply_event: malformed order_filled payload: %s", exc)
            return

        self._update_position(symbol=symbol, side=side, qty=fill_qty, price=fill_price)
        self._update_balance(cost=fill_qty * fill_price, fee=fees, fee_asset=fee_asset)

    def _apply_fill(self, req: OrderRequest, fill: BrokerFill) -> None:
        from src.execution.base import Side
        side = req.side
        self._update_position(symbol=req.symbol, side=side, qty=fill.qty, price=fill.price)
        self._update_balance(cost=fill.qty * fill.price, fee=fill.fee, fee_asset=fill.fee_asset)

    def _update_position(self, *, symbol: str, side: "Side", qty: Decimal, price: Decimal) -> None:
        from src.execution.base import Side
        key = symbol
        existing = self._positions.get(key)

        if existing is None:
            pos_side = PositionSide.LONG if side == Side.BUY else PositionSide.SHORT
            self._positions[key] = Position(
                symbol=symbol,
                side=pos_side,
                qty=qty,
                entry_price=price,
            )
            return

        if side == Side.BUY:
            if existing.side == PositionSide.LONG:
                # average into existing long
                total_qty = existing.qty + qty
                avg_price = (existing.qty * existing.entry_price + qty * price) / total_qty
                self._positions[key] = Position(
                    symbol=symbol,
                    side=PositionSide.LONG,
                    qty=total_qty,
                    entry_price=avg_price,
                )
            else:
                # buying into short — reduce or flip
                net = existing.qty - qty
                if net > Decimal("0"):
                    self._positions[key] = Position(
                        symbol=symbol,
                        side=PositionSide.SHORT,
                        qty=net,
                        entry_price=existing.entry_price,
                    )
                elif net < Decimal("0"):
                    self._positions[key] = Position(
                        symbol=symbol,
                        side=PositionSide.LONG,
                        qty=-net,
                        entry_price=price,
                    )
                else:
                    del self._positions[key]
        else:  # SELL
            if existing.side == PositionSide.SHORT:
                # average into existing short
                total_qty = existing.qty + qty
                avg_price = (existing.qty * existing.entry_price + qty * price) / total_qty
                self._positions[key] = Position(
                    symbol=symbol,
                    side=PositionSide.SHORT,
                    qty=total_qty,
                    entry_price=avg_price,
                )
            else:
                # selling into long — reduce or flip
                net = existing.qty - qty
                if net > Decimal("0"):
                    self._positions[key] = Position(
                        symbol=symbol,
                        side=PositionSide.LONG,
                        qty=net,
                        entry_price=existing.entry_price,
                    )
                elif net < Decimal("0"):
                    self._positions[key] = Position(
                        symbol=symbol,
                        side=PositionSide.SHORT,
                        qty=-net,
                        entry_price=price,
                    )
                else:
                    del self._positions[key]

    def _update_balance(self, *, cost: Decimal, fee: Decimal, fee_asset: str) -> None:
        asset = self._balance_asset
        if asset in self._balances:
            b = self._balances[asset]
            new_free = b.free - cost - fee
            self._balances[asset] = Balance(asset=asset, free=new_free, locked=b.locked)
        if fee_asset != asset and fee_asset in self._balances:
            b = self._balances[fee_asset]
            self._balances[fee_asset] = Balance(asset=fee_asset, free=b.free - fee, locked=b.locked)


class _FillStream:
    """AsyncIterator wrapper around asyncio.Queue for stream_fills Protocol compliance."""

    def __init__(self, queue: asyncio.Queue[BrokerFill]) -> None:
        self._queue = queue

    def __aiter__(self) -> AsyncIterator[BrokerFill]:
        return self

    async def __anext__(self) -> BrokerFill:
        return await self._queue.get()
