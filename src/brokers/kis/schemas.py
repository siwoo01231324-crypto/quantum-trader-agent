from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class KISOrderOutput(BaseModel):
    ODNO: str       # 주문번호
    ORD_TMD: str    # 주문시각 HHMMSS


class KISOrderResponse(BaseModel):
    rt_cd: str
    msg_cd: str
    msg1: str
    output: KISOrderOutput | None = None


class KISBalanceStock(BaseModel):
    """KIS 잔고 종목 — KIS API 가 일부 환경(특히 paper 계정 일부 응답)에서
    동일 필드를 소문자로 내려보내므로 case-insensitive 매핑.
    """
    model_config = ConfigDict(populate_by_name=True)

    PDNO: str = Field(validation_alias=AliasChoices("PDNO", "pdno"))                          # 종목코드
    PRDT_NAME: str = Field(validation_alias=AliasChoices("PRDT_NAME", "prdt_name"))           # 종목명
    HLDG_QTY: str = Field(validation_alias=AliasChoices("HLDG_QTY", "hldg_qty"))              # 보유수량
    PCHS_AVG_PRIC: str = Field(validation_alias=AliasChoices("PCHS_AVG_PRIC", "pchs_avg_pric"))  # 매입평균가
    EVLU_AMT: str = Field(validation_alias=AliasChoices("EVLU_AMT", "evlu_amt"))              # 평가금액

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
    """KIS 매수가능 — 소문자 응답 호환 (KISBalanceStock 와 같은 KIS API quirk)."""
    model_config = ConfigDict(populate_by_name=True)

    NRCVB_BUY_AMT: str = Field(validation_alias=AliasChoices("NRCVB_BUY_AMT", "nrcvb_buy_amt"))  # 매수가능금액

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


class FinancialRatio(BaseModel):
    """Single quarterly financial-ratio snapshot from KIS FHKST66430300.

    Field naming matches KIS API response keys (via stac_yymm/eps/bps/roe_val/...).
    These are PERIOD-END metrics (filed with disclosure), NOT market multiples.
    PER/PBR live in MarketMultiples (from inquire-price endpoint, FHKST01010100).
    """
    symbol: str | None = None
    fiscal_date: str | None = None          # "YYYYMM" from stac_yymm

    # fundamentals (period-end, quarterly cadence)
    eps: Decimal | None = None              # earnings per share (KRW)
    bps: Decimal | None = None              # book value per share (KRW)
    sps: Decimal | None = None              # sales per share (KRW)
    roe_val: Decimal | None = None          # return on equity (%)
    grs: Decimal | None = None              # revenue growth rate (%)
    bsop_prfi_inrt: Decimal | None = None   # operating profit margin (%)
    ntin_inrt: Decimal | None = None        # net income margin (%)
    lblt_rate: Decimal | None = None        # debt ratio (%)
    rsrv_rate: Decimal | None = None        # retained earnings rate (%)

    @field_validator("eps", "bps", "sps", "roe_val", "grs", "bsop_prfi_inrt",
                     "ntin_inrt", "lblt_rate", "rsrv_rate", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        return Decimal(str(v))


class MarketMultiples(BaseModel):
    """Market-multiple snapshot from KIS FHKST01010100 (inquire-price).

    Unlike FinancialRatio which is quarterly period-end data, these are POINT-IN-TIME
    market multiples derived from current price × latest disclosed fundamentals.
    """
    symbol: str | None = None
    per: Decimal | None = None              # price / earnings
    pbr: Decimal | None = None              # price / book
    eps: Decimal | None = None              # latest reported EPS (echoed from disclosure)
    bps: Decimal | None = None              # latest reported BPS

    @field_validator("per", "pbr", "eps", "bps", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        return Decimal(str(v))


class KISDailyBar(BaseModel):
    """Single daily OHLCV bar from KIS FHKST03010100."""
    date: str           # "YYYYMMDD" from stck_bsop_date
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_amt: float    # acml_tr_pbmn — accumulated trade amount (KRW)

    @field_validator("open", "high", "low", "close", "volume", "trade_amt", mode="before")
    @classmethod
    def _coerce_float(cls, v: Any) -> Any:
        if v is None or v == "":
            return 0.0
        return float(str(v))


class KISIntradayBar(BaseModel):
    """Single intraday OHLCV bar from KIS FHKST03010200."""
    date: str       # "YYYYMMDD" from stck_bsop_date
    time: str       # "HHMMSS"   from stck_cntg_hour
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_amt: float  # acml_tr_pbmn — accumulated trade amount (KRW)

    @field_validator("open", "high", "low", "close", "volume", "trade_amt", mode="before")
    @classmethod
    def _coerce_float(cls, v: Any) -> Any:
        if v is None or v == "":
            return 0.0
        return float(str(v))
