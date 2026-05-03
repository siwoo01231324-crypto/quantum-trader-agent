from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from src.brokers.base import OrderRequest, OrderType
from src.brokers.types import BrokerFill
from src.execution.base import MarketState, Side


DEFAULT_TAKER_FEE_BPS = Decimal("5")   # 0.05 %
DEFAULT_MAKER_FEE_BPS = Decimal("2")   # 0.02 %


class SlippageModel(Protocol):
    def estimate(
        self, *, side: Side, qty: Decimal, mid: Decimal, market: MarketState
    ) -> Decimal: ...


@dataclass
class MockMatchingEngine:
    """Phase 1 paper-trading matching engine.

    Policy:
    - market order: immediate fill at mid (zero slippage; SlippageModel hook optional)
    - limit order: fills only when price crosses the opposite best quote
    - partial_fill_enabled (#110): split qty into multiple fills when order >> ADV
    - all fills are taker (is_maker=False, Phase 1 simplification)
    """

    slippage_model: SlippageModel | None = None
    partial_fill_enabled: bool = False
    seed: int | None = None
    taker_fee_bps: Decimal = field(default_factory=lambda: DEFAULT_TAKER_FEE_BPS)
    maker_fee_bps: Decimal = field(default_factory=lambda: DEFAULT_MAKER_FEE_BPS)
    fee_asset: str = "USDT"
    _trade_counter: int = field(default=0, init=False, repr=False)
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def _make_fill(
        self,
        order: OrderRequest,
        qty: Decimal,
        price: Decimal,
    ) -> BrokerFill:
        fee_bps = self.taker_fee_bps
        fee = (qty * price * fee_bps / Decimal("10000")).quantize(
            Decimal("0.00000001")
        )
        broker_order_id = f"paper-{self._trade_counter}"
        trade_id = str(self._trade_counter)
        self._trade_counter += 1
        return BrokerFill(
            parent_id=order.client_order_id,
            broker_order_id=broker_order_id,
            client_order_id=order.client_order_id,
            trade_id=trade_id,
            qty=qty,
            price=price,
            fee=fee,
            fee_asset=self.fee_asset,
            ts=datetime.now(timezone.utc),
            is_maker=False,
        )

    def _split_qty(self, total: Decimal, adv: float) -> list[Decimal]:
        """Split total qty into N partial fills based on order/ADV ratio.

        - adv <= 0 → single full fill (fallback, avoids div-by-zero).
        - ratio < 0.1 → single full fill (small order).
        - else → N = max(2, ceil(ratio)) fills, RNG-weighted, sum == total.
        """
        if adv <= 0:
            return [total]
        ratio = float(total) / float(adv)
        if ratio < 0.1:
            return [total]
        n_fills = max(2, int(math.ceil(ratio)))

        # Weighted random partition (deterministic via self._rng).
        weights = [self._rng.uniform(0.5, 1.5) for _ in range(n_fills)]
        wsum = sum(weights)

        out: list[Decimal] = []
        remaining = total
        for w in weights[:-1]:
            frac = Decimal(str(w / wsum))
            chunk = (total * frac).quantize(Decimal("0.00000001"))
            if chunk <= 0:
                chunk = Decimal("0.00000001")
            if chunk >= remaining:
                chunk = remaining
            out.append(chunk)
            remaining -= chunk
            if remaining <= 0:
                break
        if remaining > 0:
            out.append(remaining)
        # Filter out zero-qty entries (defensive).
        out = [q for q in out if q > 0]
        # Ensure exact sum (last entry absorbs rounding).
        diff = total - sum(out)
        if diff != 0 and out:
            out[-1] = out[-1] + diff
        return out or [total]

    def match(self, order: OrderRequest, market: MarketState) -> list[BrokerFill]:
        """Return a list of BrokerFill for the order given current market state.

        Returns an empty list when the order cannot be filled (limit price miss).
        """
        mid = Decimal(str(market.tick.last))
        ask = Decimal(str(market.tick.ask))
        bid = Decimal(str(market.tick.bid))

        if order.order_type == OrderType.MARKET:
            fill_price = mid
        elif order.order_type == OrderType.LIMIT:
            if order.side == Side.BUY:
                if order.price < ask:
                    return []
            else:  # SELL
                if order.price > bid:
                    return []
            fill_price = mid
        else:
            return []

        if not self.partial_fill_enabled:
            return [self._make_fill(order, order.qty, fill_price)]

        chunks = self._split_qty(order.qty, market.adv)
        return [self._make_fill(order, q, fill_price) for q in chunks]
