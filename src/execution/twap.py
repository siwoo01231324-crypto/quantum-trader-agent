from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from .base import ChildOrder, Fill, MarketState, ParentOrder, Tick, TimeInForce


class VolatilityRegime(str, Enum):
    LOW = "LOW"
    MID = "MID"
    HIGH = "HIGH"


def vol_regime_from_spread(
    spread: float,
    low_threshold: float = 0.005,
    high_threshold: float = 0.02,
) -> VolatilityRegime:
    """Classify spread into volatility regime.

    spread is expressed as a fraction (e.g. 0.002 = 0.2%).
    """
    if spread < low_threshold:
        return VolatilityRegime.LOW
    if spread >= high_threshold:
        return VolatilityRegime.HIGH
    return VolatilityRegime.MID


class TWAPAlgo:
    """Time-Weighted Average Price: split parent qty across N slices.

    Optional volatility_weight adjusts slice timing boundaries:
    - weight > 1.0 on a slice → that slice's time window is wider (emits earlier)
    - weight < 1.0 on a slice → that slice's time window is narrower (emits later)

    Patent reference: US20210272201A1 (d) rule-based vol-regime matching frequency
    adjustment. ML engine (b) intentionally excluded.
    """

    name = "twap"

    def __init__(
        self,
        duration: timedelta,
        slice_count: int,
        volatility_weight: list[float] | None = None,
    ) -> None:
        if slice_count < 1:
            raise ValueError("slice_count must be >= 1")
        if volatility_weight is not None and len(volatility_weight) != slice_count:
            raise ValueError(
                f"volatility_weight length ({len(volatility_weight)}) must equal "
                f"slice_count ({slice_count})"
            )
        self.duration = duration
        self.slice_count = slice_count
        self._volatility_weight = volatility_weight
        self._slices_sent = 0
        self._parent: ParentOrder | None = None
        self._start_ts: datetime | None = None
        self._cancelled = False
        self._slice_qty = 0
        self._remainder = 0
        # Pre-compute cumulative time boundaries for each slice
        self._boundaries: list[float] = self._compute_boundaries()

    def _compute_boundaries(self) -> list[float]:
        """Compute cumulative time fraction boundaries [0..1] for each slice index.

        Returns a list of length slice_count where entry i is the fraction of
        total duration at which slice i should be emitted.
        """
        if self._volatility_weight is None:
            # Uniform: slice i emits at fraction i/slice_count
            return [i / self.slice_count for i in range(self.slice_count)]

        weights = self._volatility_weight
        total_weight = sum(weights)
        boundaries: list[float] = []
        cumulative = 0.0
        for w in weights:
            boundaries.append(cumulative / total_weight)
            cumulative += w
        return boundaries

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
        elapsed_fraction = elapsed / self.duration

        out: list[ChildOrder] = []
        while self._slices_sent < self.slice_count:
            boundary = self._boundaries[self._slices_sent]
            if elapsed_fraction < boundary:
                break
            qty = self._slice_qty + (
                self._remainder if self._slices_sent == self.slice_count - 1 else 0
            )
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
