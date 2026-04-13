from __future__ import annotations

from .base import ChildOrder, Fill, MarketState, ParentOrder, Tick, TimeInForce


class MarketAlgo:
    name = "market"

    def __init__(self) -> None:
        self._cancelled = False
        self._sent = False

    def plan(self, parent: ParentOrder, state: MarketState) -> list[ChildOrder]:
        if self._cancelled or self._sent:
            return []
        self._sent = True
        return [
            ChildOrder(
                parent_id=parent.order_id,
                symbol=parent.symbol,
                side=parent.side,
                qty=parent.qty,
                price=None,
                tif=TimeInForce.IOC,
            )
        ]

    def on_fill(self, fill: Fill) -> list[ChildOrder]:
        return []

    def on_market_tick(self, tick: Tick) -> list[ChildOrder]:
        return []

    def cancel(self) -> None:
        self._cancelled = True
