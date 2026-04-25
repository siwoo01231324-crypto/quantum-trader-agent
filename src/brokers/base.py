from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import AsyncIterator, Callable, Protocol, runtime_checkable

from src.execution.base import Side, TimeInForce
from src.brokers.types import BrokerFill


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class PositionSide(str, Enum):
    BOTH = "BOTH"
    LONG = "LONG"
    SHORT = "SHORT"


class MarginType(str, Enum):
    ISOLATED = "ISOLATED"
    CROSSED = "CROSSED"


class HealthStatus(str, Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


@dataclass
class OrderRequest:
    client_order_id: str
    symbol: str
    side: Side
    qty: Decimal
    order_type: OrderType
    price: Decimal | None
    tif: TimeInForce
    position_side: PositionSide = PositionSide.BOTH
    reduce_only: bool = False
    close_position: bool = False
    emergency_exit: bool = False


@dataclass
class OrderAck:
    broker_order_id: str
    client_order_id: str
    symbol: str
    status: str
    ts: datetime
    qty: Decimal | None = None
    price: Decimal | None = None


@dataclass
class Position:
    symbol: str
    side: PositionSide
    qty: Decimal
    entry_price: Decimal
    liquidation_price: Decimal | None = None
    margin_ratio: Decimal | None = None


@dataclass
class Balance:
    asset: str
    free: Decimal
    locked: Decimal


class Closeable(Protocol):
    def close(self) -> None: ...


@runtime_checkable
class BrokerAdapter(Protocol):
    name: str
    paper: bool

    def place_order(self, req: OrderRequest) -> OrderAck: ...
    def cancel_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> None: ...
    def get_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> OrderAck: ...
    def get_positions(self, symbol: str | None = None) -> list[Position]: ...
    def get_balance(self) -> list[Balance]: ...
    def stream_fills(self, on_fill: Callable[[BrokerFill], None]) -> Closeable: ...
    def ensure_leverage(self, symbol: str, leverage: int) -> None: ...
    def ensure_margin_type(self, symbol: str, mode: MarginType) -> None: ...
    def ensure_position_mode(self, *, hedge: bool) -> None: ...
    def health_check(self) -> HealthStatus: ...


# --- async Protocol ---
@runtime_checkable
class AsyncBrokerAdapter(Protocol):
    name: str
    paper: bool

    async def place_order(self, req: OrderRequest) -> OrderAck: ...
    async def cancel_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> None: ...
    async def get_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> OrderAck: ...
    async def get_positions(self, symbol: str | None = None) -> list[Position]: ...
    async def get_balance(self) -> list[Balance]: ...
    def stream_fills(self) -> AsyncIterator[BrokerFill]: ...
    async def ensure_leverage(self, symbol: str, leverage: int) -> None: ...
    async def ensure_margin_type(self, symbol: str, mode: MarginType) -> None: ...
    async def ensure_position_mode(self, *, hedge: bool) -> None: ...
    async def health_check(self) -> HealthStatus: ...
    async def aclose(self) -> None: ...
