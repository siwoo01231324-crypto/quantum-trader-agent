from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, field_validator


class PlaceOrderResponse(BaseModel):
    orderId: int
    clientOrderId: str
    symbol: str
    status: str
    origQty: Decimal
    price: Decimal
    avgPrice: Decimal = Decimal("0")
    updateTime: int

    @field_validator("origQty", "price", "avgPrice", mode="before")
    @classmethod
    def coerce_decimal(cls, v: Any) -> Decimal:
        return Decimal(str(v))


class CancelOrderResponse(BaseModel):
    orderId: int
    clientOrderId: str
    symbol: str
    status: str
    origQty: Decimal
    price: Decimal

    @field_validator("origQty", "price", mode="before")
    @classmethod
    def coerce_decimal(cls, v: Any) -> Decimal:
        return Decimal(str(v))


class GetOrderResponse(BaseModel):
    orderId: int
    clientOrderId: str
    symbol: str
    status: str
    origQty: Decimal
    price: Decimal
    avgPrice: Decimal = Decimal("0")
    updateTime: int

    @field_validator("origQty", "price", "avgPrice", mode="before")
    @classmethod
    def coerce_decimal(cls, v: Any) -> Decimal:
        return Decimal(str(v))


class PositionRisk(BaseModel):
    symbol: str
    positionAmt: Decimal
    entryPrice: Decimal
    markPrice: Decimal
    liquidationPrice: Decimal
    leverage: int
    marginType: str
    positionSide: str
    unRealizedProfit: Decimal = Decimal("0")
    notional: Decimal = Decimal("0")

    @field_validator(
        "positionAmt", "entryPrice", "markPrice", "liquidationPrice",
        "unRealizedProfit", "notional",
        mode="before",
    )
    @classmethod
    def coerce_decimal(cls, v: Any) -> Decimal:
        return Decimal(str(v))

    @field_validator("leverage", mode="before")
    @classmethod
    def coerce_int(cls, v: Any) -> int:
        return int(v)


class BalanceItem(BaseModel):
    asset: str
    balance: Decimal
    availableBalance: Decimal
    crossWalletBalance: Decimal = Decimal("0")

    @field_validator("balance", "availableBalance", "crossWalletBalance", mode="before")
    @classmethod
    def coerce_decimal(cls, v: Any) -> Decimal:
        return Decimal(str(v))


class ExchangeInfoFilter(BaseModel):
    filterType: str
    tickSize: Decimal | None = None
    stepSize: Decimal | None = None
    minQty: Decimal | None = None
    notional: Decimal | None = None
    multiplierUp: Decimal | None = None
    multiplierDown: Decimal | None = None

    @field_validator(
        "tickSize", "stepSize", "minQty", "notional", "multiplierUp", "multiplierDown",
        mode="before",
    )
    @classmethod
    def coerce_decimal(cls, v: Any) -> Decimal | None:
        if v is None:
            return None
        return Decimal(str(v))


class ExchangeInfoSymbol(BaseModel):
    symbol: str
    filters: list[ExchangeInfoFilter]
