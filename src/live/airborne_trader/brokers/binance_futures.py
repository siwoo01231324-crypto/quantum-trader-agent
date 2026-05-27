"""BinanceFuturesBroker — BrokerInterface 의 실 Binance Futures 구현.

기존 ``src/brokers/binance/async_http.py`` 의 ``AsyncBinanceFuturesClient`` 를
의존성으로 받아 BrokerInterface (place_market_order / get_mark_price /
close_position) 으로 adapter 한다.

reduce_only 강제: ``close_position`` 은 항상 ``reduce_only=True`` — 잔량 미달
일 때 broker 가 거부해 추가 진입 방지.
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

from src.brokers.base import (
    OrderRequest,
    OrderType,
    PositionSide,
)
from src.execution.base import Side, TimeInForce

from ..trader import OrderResult

logger = logging.getLogger(__name__)


class BinanceFuturesBroker:
    """Binance USDT-M Futures broker for airborne_trader.

    Constructor takes the already-initialized ``AsyncBinanceFuturesClient`` —
    upstream code (`scripts/airborne_trader_daemon.py`) is responsible for
    auth/session lifecycle. This class is a *thin adapter* to BrokerInterface.

    Order ID convention: ``f"airb-{uuid4}"`` — distinguishable in Binance UI
    from cs-tsmom / live-airborne-bb-reversal-kst-hours orders.
    """

    CLIENT_ID_PREFIX = "airb-"

    def __init__(self, client: Any) -> None:
        """``client``: src.brokers.binance.async_http.AsyncBinanceFuturesClient
        (or duck-typed mock with the same async API).
        """
        self.client = client

    @staticmethod
    def _make_client_order_id() -> str:
        # Binance newClientOrderId allows max 36 chars, alphanum + _ -
        return f"{BinanceFuturesBroker.CLIENT_ID_PREFIX}{uuid.uuid4().hex[:24]}"

    @staticmethod
    def _side_str_to_enum(side: str) -> Side:
        s = side.upper()
        if s == "BUY":
            return Side.BUY
        if s == "SELL":
            return Side.SELL
        raise ValueError(f"side must be BUY/SELL, got {side!r}")

    async def place_market_order(
        self, *, symbol: str, side: str, qty: float,
    ) -> OrderResult:
        """신규 진입 — market order, reduce_only=False.

        airborne_trader.handle_fire 에서 호출. fire.side='long' → side='BUY'.
        """
        client_id = self._make_client_order_id()
        req = OrderRequest(
            client_order_id=client_id,
            symbol=symbol,
            side=self._side_str_to_enum(side),
            qty=Decimal(str(qty)),
            order_type=OrderType.MARKET,
            price=None,
            tif=TimeInForce.GTC,
            position_side=PositionSide.BOTH,
            reduce_only=False,
            strategy_id="airborne_trader_daemon",
        )
        ack = await self.client.place_order(req, client_order_id=client_id)
        # PlaceOrderResponse 의 avgPrice 가 없을 수도 (MARKET 즉시 체결이라 보통 있음)
        avg_price = float(getattr(ack, "avg_price", 0) or 0)
        filled_qty = float(getattr(ack, "filled_qty", qty) or qty)
        return OrderResult(
            symbol=symbol, side=side,
            filled_qty=filled_qty, avg_price=avg_price,
            raw_response={"client_order_id": client_id, "ack": ack},
        )

    async def get_mark_price(self, symbol: str) -> float:
        """Binance public ``/fapi/v1/premiumIndex`` — mark_price (unsigned).

        client 가 unsigned GET 지원 안 하면 시스템 _get(path, signed=False) 호출.
        실패 시 0.0 (caller 는 0 이면 skip).
        """
        try:
            raw = await self.client._get(
                "/fapi/v1/premiumIndex",
                params={"symbol": symbol},
                signed=False,
            )
        except Exception as err:  # noqa: BLE001
            logger.warning(
                "[BinanceFuturesBroker] get_mark_price %s failed: %s",
                symbol, err,
            )
            return 0.0
        if isinstance(raw, list):
            # symbol 명시했어도 list 응답할 수 있음
            raw = next((r for r in raw if r.get("symbol") == symbol), None)
        if not raw:
            return 0.0
        try:
            return float(raw.get("markPrice") or 0)
        except (TypeError, ValueError):
            return 0.0

    async def close_position(
        self, *, symbol: str, side: str, qty: float,
    ) -> OrderResult:
        """청산 — opposite side market order + ``reduce_only=True``.

        ``side`` 는 *원래 포지션* 의 방향 ('BUY' = long 포지션). 청산은 반대로.
        airborne_trader.trader._maybe_close 에서 변환된 side 가 들어옴.
        """
        # caller 가 이미 opposite side 를 넘긴다고 가정 (trader.py 의 close_position
        # caller). 본 메서드는 그 side 로 reduce_only=True 발주.
        client_id = self._make_client_order_id()
        req = OrderRequest(
            client_order_id=client_id,
            symbol=symbol,
            side=self._side_str_to_enum(side),
            qty=Decimal(str(qty)),
            order_type=OrderType.MARKET,
            price=None,
            tif=TimeInForce.GTC,
            position_side=PositionSide.BOTH,
            reduce_only=True,
            strategy_id="airborne_trader_daemon",
        )
        ack = await self.client.place_order(req, client_order_id=client_id)
        avg_price = float(getattr(ack, "avg_price", 0) or 0)
        filled_qty = float(getattr(ack, "filled_qty", qty) or qty)
        return OrderResult(
            symbol=symbol, side=side,
            filled_qty=filled_qty, avg_price=avg_price,
            raw_response={"client_order_id": client_id, "ack": ack, "reduce_only": True},
        )

    async def get_open_position_qty(self, symbol: str) -> float:
        """Reconciler 용 — broker 측 현재 포지션 수량 (NET long+, short−, flat 0).

        ``get_position_risk(symbol)`` → positionAmt 합산. multi-account hedge
        mode 까지 일단 BOTH 가정 (cs-tsmom / live-airborne-kst-hours 도 동일).
        """
        try:
            positions = await self.client.get_position_risk(symbol=symbol)
        except Exception as err:  # noqa: BLE001
            logger.warning(
                "[BinanceFuturesBroker] get_position_risk %s failed: %s",
                symbol, err,
            )
            return 0.0
        total = 0.0
        for p in positions or []:
            try:
                total += float(getattr(p, "position_amt", 0) or 0)
            except (TypeError, ValueError):
                continue
        return total
