"""Bitget v2 USDT-M Futures REST response dataclasses.

Frozen dataclasses (Decimal-safe) mirror the json wire format with minimal
field renaming. Caller adapter translates to the broker-agnostic
``OrderAck`` / ``Position`` / ``Balance`` types.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


def _dec(v: Any, default: str = "0") -> Decimal:
    if v is None or v == "":
        return Decimal(default)
    return Decimal(str(v))


@dataclass(frozen=True, slots=True)
class PlaceOrderResponse:
    """Bitget v2 ``/api/v2/mix/order/place-order`` response."""
    orderId: str
    clientOid: str

    @classmethod
    def from_json(cls, data: dict) -> "PlaceOrderResponse":
        return cls(
            orderId=str(data["orderId"]),
            clientOid=str(data.get("clientOid", "")),
        )


@dataclass(frozen=True, slots=True)
class OrderDetailResponse:
    """Bitget v2 ``/api/v2/mix/order/detail`` response.

    Bitget fields: orderId / clientOid / symbol / size / price / priceAvg /
    status / side / orderType / cTime / uTime / fee.

    status values: ``live`` (open) / ``partially_filled`` / ``filled`` /
    ``canceled`` / ``not_existed``.
    """
    orderId: str
    clientOid: str
    symbol: str
    size: Decimal
    price: Decimal | None
    priceAvg: Decimal | None
    status: str
    side: str
    orderType: str
    filledSize: Decimal
    ctime: int  # ms
    utime: int  # ms

    @classmethod
    def from_json(cls, data: dict) -> "OrderDetailResponse":
        return cls(
            orderId=str(data["orderId"]),
            clientOid=str(data.get("clientOid", "")),
            symbol=str(data["symbol"]),
            size=_dec(data.get("size")),
            price=_dec(data["price"]) if data.get("price") else None,
            priceAvg=_dec(data["priceAvg"]) if data.get("priceAvg") else None,
            status=str(data["state"]) if "state" in data else str(data.get("status", "")),
            side=str(data["side"]),
            orderType=str(data.get("orderType", "")),
            filledSize=_dec(data.get("baseVolume") or data.get("filledSize", "0")),
            ctime=int(data.get("cTime", 0)),
            utime=int(data.get("uTime", 0)),
        )


@dataclass(frozen=True, slots=True)
class PositionResponse:
    """Bitget v2 ``/api/v2/mix/position/single-position`` / ``all-position``.

    holdSide: ``long`` or ``short`` (Bitget의 한쪽-방향 표시).
    total: 보유 수량 (양수 — side 가 부호 역할).
    averageOpenPrice: 진입 가격.
    markPrice: 현재 mark price.
    leverage: 종목 레버리지 (string).
    """
    symbol: str
    holdSide: str
    total: Decimal
    available: Decimal
    averageOpenPrice: Decimal
    markPrice: Decimal
    leverage: int
    marginMode: str  # "crossed" | "isolated"
    unrealizedPL: Decimal
    liquidationPrice: Decimal | None
    marginCoin: str

    @classmethod
    def from_json(cls, data: dict) -> "PositionResponse":
        return cls(
            symbol=str(data["symbol"]),
            holdSide=str(data.get("holdSide", "long")),
            total=_dec(data.get("total", "0")),
            available=_dec(data.get("available", "0")),
            averageOpenPrice=_dec(data.get("openPriceAvg") or data.get("averageOpenPrice", "0")),
            markPrice=_dec(data.get("markPrice", "0")),
            leverage=int(data.get("leverage", "1")) if data.get("leverage") else 1,
            marginMode=str(data.get("marginMode", "crossed")),
            unrealizedPL=_dec(data.get("unrealizedPL", "0")),
            liquidationPrice=(
                _dec(data["liquidationPrice"])
                if data.get("liquidationPrice") and data["liquidationPrice"] != "" else None
            ),
            marginCoin=str(data.get("marginCoin", "USDT")),
        )


@dataclass(frozen=True, slots=True)
class AccountResponse:
    """Bitget v2 ``/api/v2/mix/account/account`` response (single asset)."""
    marginCoin: str
    available: Decimal
    locked: Decimal
    accountEquity: Decimal
    usdtEquity: Decimal
    crossedMaxAvailable: Decimal

    @classmethod
    def from_json(cls, data: dict) -> "AccountResponse":
        return cls(
            marginCoin=str(data.get("marginCoin", "USDT")),
            available=_dec(data.get("available", "0")),
            locked=_dec(data.get("locked", "0")),
            accountEquity=_dec(data.get("accountEquity", "0")),
            usdtEquity=_dec(data.get("usdtEquity", "0")),
            crossedMaxAvailable=_dec(data.get("crossedMaxAvailable", "0")),
        )


@dataclass(frozen=True, slots=True)
class ContractResponse:
    """Bitget v2 ``/api/v2/mix/market/contracts`` entry.

    Used for LOT_SIZE-equivalent quantization and minimum-notional checks.
    """
    symbol: str
    baseCoin: str
    quoteCoin: str
    minTradeNum: Decimal       # min order qty in base units (LOT minQty)
    sizeMultiplier: Decimal    # qty step (LOT stepSize)
    priceEndStep: Decimal      # price tick (Bitget naming: pricePlace + priceEndStep)
    pricePlace: int            # price decimals
    volumePlace: int           # qty decimals

    @classmethod
    def from_json(cls, data: dict) -> "ContractResponse":
        # priceEndStep + pricePlace describe tick — actual tick = priceEndStep / 10**pricePlace.
        return cls(
            symbol=str(data["symbol"]),
            baseCoin=str(data.get("baseCoin", "")),
            quoteCoin=str(data.get("quoteCoin", "USDT")),
            minTradeNum=_dec(data.get("minTradeNum", "0")),
            sizeMultiplier=_dec(data.get("sizeMultiplier", "1")),
            priceEndStep=_dec(data.get("priceEndStep", "1")),
            pricePlace=int(data.get("pricePlace", "0")) if data.get("pricePlace") else 0,
            volumePlace=int(data.get("volumePlace", "0")) if data.get("volumePlace") else 0,
        )
