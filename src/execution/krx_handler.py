from __future__ import annotations

from collections import deque
from enum import Enum

from .base import ChildOrder, MarketState


class SingleAuctionPolicy(str, Enum):
    WAIT = "WAIT"
    PARTICIPATE_AT_REFERENCE = "PARTICIPATE_AT_REFERENCE"
    CANCEL = "CANCEL"


class KRXSingleAuctionHandler:
    """Buffers child orders during KRX single-price auction / halt windows.

    Behavior depends on policy:
      - WAIT: queue orders, release when continuous trading resumes.
      - PARTICIPATE_AT_REFERENCE: rewrite as limit at reference price and pass through.
      - CANCEL: drop orders and require caller to re-plan.
    """

    def __init__(self, policy: SingleAuctionPolicy = SingleAuctionPolicy.WAIT) -> None:
        self.policy = policy
        self._queue: deque[ChildOrder] = deque()

    def filter(self, orders: list[ChildOrder], state: MarketState) -> list[ChildOrder]:
        if state.halted:
            if self.policy == SingleAuctionPolicy.CANCEL:
                return []
            self._queue.extend(orders)
            return []
        if state.in_single_auction:
            if self.policy == SingleAuctionPolicy.CANCEL:
                return []
            if self.policy == SingleAuctionPolicy.WAIT:
                self._queue.extend(orders)
                return []
            # PARTICIPATE_AT_REFERENCE: rewrite to limit @ last
            ref = state.tick.last
            return [_with_price(o, ref) for o in orders]
        # continuous trading: flush queue first
        flushed = list(self._queue)
        self._queue.clear()
        return flushed + orders

    @property
    def queued(self) -> int:
        return len(self._queue)


def _with_price(order: ChildOrder, price: float) -> ChildOrder:
    return ChildOrder(
        parent_id=order.parent_id,
        symbol=order.symbol,
        side=order.side,
        qty=order.qty,
        price=price,
        tif=order.tif,
        post_only=order.post_only,
        ts=order.ts,
    )
