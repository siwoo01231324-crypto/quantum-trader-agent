from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TimeInForce(str, Enum):
    DAY = "DAY"
    IOC = "IOC"
    FOK = "FOK"


@dataclass
class ParentOrder:
    order_id: str
    symbol: str
    side: Side
    qty: int
    tif: TimeInForce = TimeInForce.DAY
    deadline: datetime | None = None
    algo_params: dict = field(default_factory=dict)


@dataclass
class ChildOrder:
    parent_id: str
    symbol: str
    side: Side
    qty: int
    price: float | None  # None == market
    tif: TimeInForce = TimeInForce.DAY
    post_only: bool = False
    ts: datetime | None = None


@dataclass
class Fill:
    parent_id: str
    qty: int
    price: float
    ts: datetime
    fee: float = 0.0


@dataclass
class Tick:
    symbol: str
    bid: float
    ask: float
    last: float
    volume: int
    ts: datetime


@dataclass
class MarketState:
    tick: Tick
    in_single_auction: bool = False
    halted: bool = False
    adv: float = 0.0  # average daily volume


class SlippageModel(Protocol):
    def estimate(self, child: ChildOrder, state: MarketState) -> float: ...


@runtime_checkable
class ExecutionAlgorithm(Protocol):
    name: str

    def plan(self, parent: ParentOrder, state: MarketState) -> list[ChildOrder]: ...

    def on_fill(self, fill: Fill) -> list[ChildOrder]: ...

    def on_market_tick(self, tick: Tick) -> list[ChildOrder]: ...

    def cancel(self) -> None: ...
