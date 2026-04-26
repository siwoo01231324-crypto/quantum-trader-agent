from __future__ import annotations

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
