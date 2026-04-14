from __future__ import annotations

from datetime import datetime, timedelta

from .base import ChildOrder, Fill, MarketState, ParentOrder, Tick, TimeInForce


class TWAPAlgo:
    """Time-Weighted Average Price: split parent qty evenly across N slices."""

    name = "twap"

    def __init__(self, duration: timedelta, slice_count: int) -> None:
        if slice_count < 1:
            raise ValueError("slice_count must be >= 1")
        self.duration = duration
        self.slice_count = slice_count
        self._slices_sent = 0
        self._parent: ParentOrder | None = None
        self._start_ts: datetime | None = None
        self._cancelled = False
        self._slice_qty = 0
        self._remainder = 0

    def plan(self, parent: ParentOrder, state: MarketState) -> list[ChildOrder]:
        self._parent = parent
        self._start_ts = state.tick.ts
        self._slice_qty = parent.qty // self.slice_count
        self._remainder = parent.qty - self._slice_qty * self.slice_count
        return self._maybe_emit(state.tick)

    def on_fill(self, fill: Fill) -> list[ChildOrder]:
        return []

    def on_market_tick(self, tick: Tick) -> list[ChildOrder]:
        return self._maybe_emit(tick)

    def cancel(self) -> None:
        self._cancelled = True

    def _maybe_emit(self, tick: Tick) -> list[ChildOrder]:
        if self._cancelled or self._parent is None or self._start_ts is None:
            return []
        if self._slices_sent >= self.slice_count:
            return []
        elapsed = tick.ts - self._start_ts
        slice_dur = self.duration / self.slice_count
        target_idx = min(int(elapsed / slice_dur), self.slice_count - 1)
        out: list[ChildOrder] = []
        while self._slices_sent <= target_idx and self._slices_sent < self.slice_count:
            qty = self._slice_qty + (self._remainder if self._slices_sent == self.slice_count - 1 else 0)
            if qty > 0:
                out.append(
                    ChildOrder(
                        parent_id=self._parent.order_id,
                        symbol=self._parent.symbol,
                        side=self._parent.side,
                        qty=qty,
                        price=None,
                        tif=TimeInForce.IOC,
                        ts=tick.ts,
                    )
                )
            self._slices_sent += 1
        return out
