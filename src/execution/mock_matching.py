from __future__ import annotations

import math
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
class SquareRootImpact:
    """Market-impact slippage model: I = sign * k * sigma * mid * sqrt(qty / ADV).

    Parameters
    ----------
    k:     impact constant (dimensionless), default 1.0
    sigma: volatility estimate (fraction of price), default 0.01 (1%)

    Returns a signed Decimal price delta: positive for BUY, negative for SELL.
    When ADV == 0 the impact is zero to avoid division by zero.
    """

    k: Decimal = field(default_factory=lambda: Decimal("1.0"))
    sigma: Decimal = field(default_factory=lambda: Decimal("0.01"))

    def estimate(
        self, *, side: Side, qty: Decimal, mid: Decimal, market: MarketState
    ) -> Decimal:
        adv = market.adv
        if adv == 0.0:
            return Decimal("0")

        participation = float(qty) / adv
        impact_magnitude = self.k * self.sigma * mid * Decimal(str(math.sqrt(participation)))
        sign = Decimal("1") if side == Side.BUY else Decimal("-1")
        return sign * impact_magnitude


@dataclass
class MockMatchingEngine:
    """Phase 1 paper-trading matching engine.

    Policy:
    - market order: immediate 100% fill at mid (zero slippage)
    - limit order: fills only when price crosses the opposite best quote
    - partial fills disabled (Phase 1)
    - all fills are taker (is_maker=False, Phase 1 simplification)
    """

    slippage_model: SlippageModel | None = None
    partial_fill_enabled: bool = False
    taker_fee_bps: Decimal = field(default_factory=lambda: DEFAULT_TAKER_FEE_BPS)
    maker_fee_bps: Decimal = field(default_factory=lambda: DEFAULT_MAKER_FEE_BPS)
    fee_asset: str = "USDT"
    _trade_counter: int = field(default=0, init=False, repr=False)

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

        if self.slippage_model is not None:
            fill_price = fill_price + self.slippage_model.estimate(
                side=order.side, qty=order.qty, mid=mid, market=market
            )

        fill_qty = order.qty
        fee_bps = self.taker_fee_bps
        fee = (fill_qty * fill_price * fee_bps / Decimal("10000")).quantize(
            Decimal("0.00000001")
        )

        broker_order_id = f"paper-{self._trade_counter}"
        trade_id = str(self._trade_counter)
        self._trade_counter += 1

        fill = BrokerFill(
            parent_id=order.client_order_id,
            broker_order_id=broker_order_id,
            client_order_id=order.client_order_id,
            trade_id=trade_id,
            qty=fill_qty,
            price=fill_price,
            fee=fee,
            fee_asset=self.fee_asset,
            ts=datetime.now(timezone.utc),
            is_maker=False,
        )
        return [fill]
