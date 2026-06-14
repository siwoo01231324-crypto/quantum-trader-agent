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
    # 2026-06-14 — fill 자체가 들고 오는 종목/방향 (Bitget orders 채널의 instId/side).
    # 기본 "" 라 기존 생성부(binance/kis/mock 등)는 byte-identical. 거래소 네이티브
    # TP/SL 청산처럼 우리가 coid 를 등록 안 한 fill 도 symbol 을 잃지 않게 해
    # store replay 가 청산을 drop 하던 누적 인플레이션을 차단한다 (fill_consumer 참조).
    symbol: str = ""
    side: str = ""

    def __post_init__(self) -> None:
        _require_decimal("qty", self.qty)
        _require_decimal("price", self.price)
        _require_decimal("fee", self.fee)
