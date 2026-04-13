from __future__ import annotations

from .base import ChildOrder, Fill, MarketState, ParentOrder, Tick, TimeInForce


class LimitAlgo:
    name = "limit"

    def __init__(self, price: float, tif: TimeInForce = TimeInForce.DAY, post_only: bool = False) -> None:
        self.price = price
        self.tif = tif
        self.post_only = post_only
        self._sent = False
        self._cancelled = False

    def plan(self, parent: ParentOrder, state: MarketState) -> list[ChildOrder]:
        if self._sent or self._cancelled:
            return []
        self._sent = True
        return [
            ChildOrder(
                parent_id=parent.order_id,
                symbol=parent.symbol,
                side=parent.side,
                qty=parent.qty,
                price=self.price,
                tif=self.tif,
                post_only=self.post_only,
            )
        ]

    def on_fill(self, fill: Fill) -> list[ChildOrder]:
        return []

    def on_market_tick(self, tick: Tick) -> list[ChildOrder]:
        return []

    def cancel(self) -> None:
        self._cancelled = True
