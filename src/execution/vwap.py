from __future__ import annotations

from .base import ChildOrder, Fill, MarketState, ParentOrder, Tick, TimeInForce

_DEFAULT_ALPHA = 0.5


class VWAPAlgo:
    """Volume-Weighted Average Price execution algorithm.

    Supports real-time volume profile blending (patent #84-1 adaptation):
    remaining slice weights = (historical_ratio * alpha) + (live_ratio * (1-alpha))

    alpha=1.0 -> pure historical (static, backwards-compatible)
    alpha=0.0 -> pure live data
    Default alpha=0.5 (balanced blend)

    Invariant: weights[idx:] always sums to 1.0 (proportions of remaining qty).
    Child order qty = int(remaining_qty * weights[idx]) for non-last slices.
    Last slice always gets the full remainder to conserve total quantity.
    """

    name = "vwap"

    def __init__(
        self,
        volume_profile: list[float],
        participation_rate: float = 0.1,
        algo_params: dict | None = None,
    ) -> None:
        if not volume_profile:
            raise ValueError("volume_profile must be non-empty")
        if not 0 < participation_rate <= 1:
            raise ValueError("participation_rate must be in (0, 1]")
        total = sum(volume_profile)
        if total <= 0:
            raise ValueError("volume_profile must sum > 0")
        # weights[i] = proportion of total qty, normalized to sum=1
        # This is the canonical form: weights[idx:] always sums to 1 since
        # we renormalize remaining weights after each slice is consumed.
        self.weights = [v / total for v in volume_profile]
        self._hist_weights = list(self.weights)  # immutable historical reference
        self.participation_rate = participation_rate
        self._algo_params: dict = algo_params or {}
        self._idx = 0
        self._parent: ParentOrder | None = None
        self._cancelled = False
        self._sent_qty = 0

    def plan(self, parent: ParentOrder, state: MarketState) -> list[ChildOrder]:
        self._parent = parent
        if parent.algo_params:
            self._algo_params = {**self._algo_params, **parent.algo_params}
        return self._emit_next(state.tick)

    def on_fill(self, fill: Fill) -> list[ChildOrder]:
        return []

    def on_market_tick(
        self,
        tick: Tick,
        realized_volume: int = 0,
        in_auction: bool = False,
    ) -> list[ChildOrder]:
        if in_auction:
            return []
        if realized_volume > 0:
            self._blend_weights(realized_volume)
        return self._emit_next(tick)

    def cancel(self) -> None:
        self._cancelled = True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _renormalize_remaining(self) -> None:
        """Renormalize weights[idx:] so they sum to 1."""
        remaining_weights = self.weights[self._idx :]
        total = sum(remaining_weights)
        if total <= 0:
            return
        for i, w in enumerate(remaining_weights):
            self.weights[self._idx + i] = w / total

    def _blend_weights(self, realized_volume: int) -> None:
        """Blend remaining weights with live realized-volume distribution.

        Formula per remaining bucket i:
            blended_i = alpha * hist_i/hist_total + (1-alpha) * (1/n_remaining)
        Renormalized to sum=1 over remaining buckets.
        """
        remaining = len(self.weights) - self._idx
        if remaining <= 0:
            return

        alpha = float(self._algo_params.get("vwap_alpha", _DEFAULT_ALPHA))
        alpha = max(0.0, min(1.0, alpha))

        if alpha == 1.0:
            return

        hist_remain = self._hist_weights[self._idx :]
        hist_total = sum(hist_remain)
        if hist_total <= 0:
            return

        live_weight = 1.0 / remaining  # uniform live distribution per bucket

        blended = []
        for h in hist_remain:
            hist_ratio = h / hist_total
            blended.append(alpha * hist_ratio + (1.0 - alpha) * live_weight)

        blended_total = sum(blended)
        if blended_total <= 0:
            return

        # Store renormalized so weights[idx:] sum to 1
        for i, b in enumerate(blended):
            self.weights[self._idx + i] = b / blended_total

    def _emit_next(self, tick: Tick) -> list[ChildOrder]:
        if self._cancelled or self._parent is None:
            return []
        if self._idx >= len(self.weights):
            return []

        # Ensure remaining weights sum to 1 before reading (handles initial state
        # where weights[0] = proportion-of-total; after first emit we renormalize)
        self._renormalize_remaining()

        remaining_qty = self._parent.qty - self._sent_qty
        weight = self.weights[self._idx]
        self._idx += 1
        is_last = self._idx >= len(self.weights)

        if is_last:
            qty = remaining_qty
        else:
            qty = int(remaining_qty * weight)

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
