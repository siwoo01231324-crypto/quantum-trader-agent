from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


def _require_decimal(name: str, value: object) -> Decimal:
    if isinstance(value, float):
        raise TypeError(f"{name} must be Decimal, not float")
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be Decimal, got {type(value).__name__}")
    return value


@dataclass(frozen=True)
class BrokerFill:
    """Decimal-precision fill from a broker. Never uses float for monetary fields."""

    parent_id: str
    broker_order_id: str
    client_order_id: str
    trade_id: str          # (broker_order_id, trade_id) pair used for dedup
    qty: Decimal
    price: Decimal
    fee: Decimal
    fee_asset: str         # e.g. "USDT" or "KRW"
    ts: datetime
    is_maker: bool

    def __post_init__(self) -> None:
        _require_decimal("qty", self.qty)
        _require_decimal("price", self.price)
        _require_decimal("fee", self.fee)
