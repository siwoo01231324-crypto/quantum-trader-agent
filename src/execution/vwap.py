from __future__ import annotations

from .base import ChildOrder, Fill, MarketState, ParentOrder, Tick, TimeInForce


class VWAPAlgo:
    """Volume-Weighted Average Price: emit child orders proportional to historical volume profile."""

    name = "vwap"

    def __init__(self, volume_profile: list[float], participation_rate: float = 0.1) -> None:
        if not volume_profile:
            raise ValueError("volume_profile must be non-empty")
        if not 0 < participation_rate <= 1:
            raise ValueError("participation_rate must be in (0, 1]")
        total = sum(volume_profile)
        if total <= 0:
            raise ValueError("volume_profile must sum > 0")
        self.weights = [v / total for v in volume_profile]
        self.participation_rate = participation_rate
        self._idx = 0
        self._parent: ParentOrder | None = None
        self._cancelled = False
        self._sent_qty = 0

    def plan(self, parent: ParentOrder, state: MarketState) -> list[ChildOrder]:
        self._parent = parent
        return self._emit_next(state.tick)

    def on_fill(self, fill: Fill) -> list[ChildOrder]:
        return []

    def on_market_tick(self, tick: Tick) -> list[ChildOrder]:
        return self._emit_next(tick)

    def cancel(self) -> None:
        self._cancelled = True

    def _emit_next(self, tick: Tick) -> list[ChildOrder]:
        if self._cancelled or self._parent is None:
            return []
        if self._idx >= len(self.weights):
            return []
        weight = self.weights[self._idx]
        self._idx += 1
        is_last = self._idx >= len(self.weights)
        qty = self._parent.qty - self._sent_qty if is_last else int(self._parent.qty * weight)
        if qty <= 0:
            return []
        self._sent_qty += qty
        return [
            ChildOrder(
                parent_id=self._parent.order_id,
                symbol=self._parent.symbol,
                side=self._parent.side,
                qty=qty,
                price=None,
                tif=TimeInForce.IOC,
                ts=tick.ts,
            )
        ]
