from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, field_validator


class KISOrderOutput(BaseModel):
    ODNO: str       # 주문번호
    ORD_TMD: str    # 주문시각 HHMMSS


class KISOrderResponse(BaseModel):
    rt_cd: str
    msg_cd: str
    msg1: str
    output: KISOrderOutput | None = None


class KISBalanceStock(BaseModel):
    PDNO: str           # 종목코드
    PRDT_NAME: str      # 종목명
    HLDG_QTY: str       # 보유수량
    PCHS_AVG_PRIC: str  # 매입평균가
    EVLU_AMT: str       # 평가금액

    @property
    def qty(self) -> Decimal:
        return Decimal(self.HLDG_QTY)

    @property
    def avg_price(self) -> Decimal:
        return Decimal(self.PCHS_AVG_PRIC)


class KISBalanceOutput(BaseModel):
    output1: list[KISBalanceStock] = []
    output2: list[dict[str, Any]] = []


class KISBalanceResponse(BaseModel):
    rt_cd: str
    msg_cd: str
    msg1: str
    output1: list[KISBalanceStock] = []
    output2: list[dict[str, Any]] = []


class KISBuyableOutput(BaseModel):
    NRCVB_BUY_AMT: str   # 매수가능금액

    @property
    def buyable_amount(self) -> Decimal:
        return Decimal(self.NRCVB_BUY_AMT)


class KISBuyableResponse(BaseModel):
    rt_cd: str
    msg_cd: str
    msg1: str
    output: KISBuyableOutput | None = None


class KISTokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    access_token_token_expired: str
