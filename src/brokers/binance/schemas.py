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
    # 2026-05-22 post-only Maker — 누적 체결 수량. post-only LIMIT 미체결
    # fallback 이 잔량(origQty - executedQty)만 시장가로 재발주할 때 필요.
    # 부분 체결(PARTIALLY_FILLED) 주문은 cancel 후에도 이 값이 유효하다.
    executedQty: Decimal = Decimal("0")
    price: Decimal
    avgPrice: Decimal = Decimal("0")
    updateTime: int

    @field_validator("origQty", "executedQty", "price", "avgPrice", mode="before")
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


class IncomeItem(BaseModel):
    """`/fapi/v1/income` 레코드 1건 — 자금 변동 원장 (실현손익·수수료·펀딩 등).

    ``incomeType`` ∈ {REALIZED_PNL, COMMISSION, FUNDING_FEE, TRANSFER,
    WELCOME_BONUS, INSURANCE_CLEAR, ...}. ``income`` 은 부호 있는 금액 —
    COMMISSION 은 음수, FUNDING_FEE / REALIZED_PNL 은 ±. ``symbol`` 은
    TRANSFER 등 일부 타입에서 빈 문자열일 수 있다.

    대시보드 실현손익(NET) = Σ REALIZED_PNL + Σ COMMISSION + Σ FUNDING_FEE —
    거래소 화면의 실현손익과 정확히 일치하는 권위 출처.
    """

    symbol: str = ""
    incomeType: str
    income: Decimal
    asset: str = ""
    time: int
    tranId: int = 0
    tradeId: str = ""

    @field_validator("income", mode="before")
    @classmethod
    def _coerce_income(cls, v: Any) -> Decimal:
        return Decimal(str(v))

    @field_validator("tradeId", mode="before")
    @classmethod
    def _coerce_trade_id(cls, v: Any) -> str:
        return "" if v is None else str(v)

    @field_validator("tranId", mode="before")
    @classmethod
    def _coerce_tran_id(cls, v: Any) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0


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
