from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from src.brokers import client_id as client_id_mod
from src.brokers.base import (
    Balance,
    BrokerAdapter,
    HealthStatus,
    MarginType,
    OrderAck,
    OrderRequest,
    Position,
    PositionSide,
)
from src.brokers.binance.rest import BinanceFuturesClient
from src.brokers.binance.symbol_filters import SymbolFilters
from src.brokers.errors import BrokerStartupError, ValidationError
from src.brokers.rate_limiter import RateLimiter
from src.brokers.types import BrokerFill

log = logging.getLogger(__name__)


class BinanceFuturesAdapter:
    """High-level BrokerAdapter wrapping BinanceFuturesClient."""

    name = "binance_futures"

    def __init__(
        self,
        api_key: str,
        secret: str,
        base_url: str,
        paper: bool = True,
        kill_switch: object | None = None,
    ) -> None:
        self.paper = paper
        self._kill_switch = kill_switch

        rate_limiter = RateLimiter()
        rate_limiter.register_bucket("weight", rate=100.0, capacity=6000.0)
        rate_limiter.register_bucket("orders_1m", rate=20.0, capacity=1200.0)
        rate_limiter.register_bucket("orders_10s", rate=30.0, capacity=300.0)

        self._client = BinanceFuturesClient(
            api_key=api_key,
            secret=secret,
            base_url=base_url,
            rate_limiter=rate_limiter,
        )
        self._symbol_filters = SymbolFilters(base_url=base_url)
        self._hedge_mode: bool | None = None  # populated by ensure_position_mode

    # ── kill switch gate ─────────────────────────────────────────────────────

    def _assert_allow_order(self, emergency_exit: bool) -> None:
        if self._kill_switch is not None:
            self._kill_switch.assert_allow_order(liquidation=emergency_exit)

    # ── BrokerAdapter methods ────────────────────────────────────────────────

    def place_order(self, req: OrderRequest) -> OrderAck:
        self._assert_allow_order(req.emergency_exit)

        import re as _re
        from src.brokers.client_id import BINANCE_CLIENT_ID_PATTERN, generate
        pattern = _re.compile(BINANCE_CLIENT_ID_PATTERN)
        if pattern.match(req.client_order_id):
            cid = req.client_order_id
        else:
            cid = generate(
                strategy="fallback",
                symbol=req.symbol,
                side=req.side.value,
                ts_ms=self._client._now_ms(),
            )
            log.warning(
                "client_order_id %r failed Binance regex; using generated %r",
                req.client_order_id,
                cid,
            )

        resp = self._client.place_order(req, cid)
        return OrderAck(
            broker_order_id=str(resp.orderId),
            client_order_id=resp.clientOrderId,
            symbol=resp.symbol,
            status=resp.status,
            ts=datetime.fromtimestamp(resp.updateTime / 1000, tz=timezone.utc),
            qty=resp.origQty,
            price=resp.price if resp.price != Decimal("0") else None,
        )

    def cancel_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> None:
        self._client.cancel_order(
            symbol,
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
        )

    def get_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> OrderAck:
        resp = self._client.get_order(
            symbol,
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
        )
        return OrderAck(
            broker_order_id=str(resp.orderId),
            client_order_id=resp.clientOrderId,
            symbol=resp.symbol,
            status=resp.status,
            ts=datetime.fromtimestamp(resp.updateTime / 1000, tz=timezone.utc),
            qty=resp.origQty,
            price=resp.price if resp.price != Decimal("0") else None,
        )

    def get_positions(self, symbol: str | None = None) -> list[Position]:
        risks = self._client.get_position_risk(symbol)
        positions = []
        for r in risks:
            if r.positionAmt == Decimal("0"):
                continue
            side = PositionSide(r.positionSide)
            positions.append(
                Position(
                    symbol=r.symbol,
                    side=side,
                    qty=abs(r.positionAmt),
                    entry_price=r.entryPrice,
                    liquidation_price=r.liquidationPrice if r.liquidationPrice != Decimal("0") else None,
                )
            )
        return positions

    def get_balance(self) -> list[Balance]:
        items = self._client.get_balance()
        return [
            Balance(
                asset=b.asset,
                free=b.availableBalance,
                locked=b.balance - b.availableBalance,
            )
            for b in items
        ]

    def stream_fills(self, on_fill: Callable[[BrokerFill], None]) -> object:
        raise NotImplementedError("stream_fills requires ws.py (Task #5)")

    def health_check(self) -> HealthStatus:
        try:
            self._client._get("/fapi/v1/ping")
            return HealthStatus.OK
        except Exception:
            return HealthStatus.DOWN

    # ── ensure_* (idempotent) ─────────────────────────────────────────────────

    def ensure_leverage(self, symbol: str, leverage: int) -> None:
        risks = self._client.get_position_risk(symbol)
        if risks and risks[0].leverage == leverage:
            return
        self._client.set_leverage(symbol, leverage)

    def ensure_margin_type(self, symbol: str, mode: MarginType) -> None:
        risks = self._client.get_position_risk(symbol)
        if risks:
            current = risks[0].marginType.upper()
            if current == mode.value:
                return
        self._client.set_margin_type(symbol, mode)

    def ensure_position_mode(self, *, hedge: bool) -> None:
        current_hedge = self._client.get_position_mode()
        if current_hedge != hedge:
            raise BrokerStartupError(
                f"Position mode mismatch: expected hedge={hedge}, "
                f"but exchange has hedge={current_hedge}. "
                "Change position mode manually before starting the adapter."
            )
        self._hedge_mode = hedge

    # ── Protective orders (#127) ────────────────────────────────────────────
    # STOP_MARKET / TAKE_PROFIT_MARKET 보호 주문은 schema 확장 없이 raw REST 로
    # 전송. ProtectiveOrderManager 가 이 메소드를 통해 broker 에 등록한다.

    def place_protective_order(
        self,
        *,
        symbol: str,
        side: str,            # "BUY" or "SELL" — 진입 반대방향
        qty: Decimal,
        stop_price: Decimal,
        kind: str,            # "STOP_MARKET" or "TAKE_PROFIT_MARKET"
    ) -> str:
        """Submit a Binance Futures STOP_MARKET / TAKE_PROFIT_MARKET reduceOnly order.

        Returns broker order id (orderId from the exchange response).
        """
        self._assert_allow_order(emergency_exit=False)
        if kind not in ("STOP_MARKET", "TAKE_PROFIT_MARKET"):
            raise ValueError(f"unsupported protective kind: {kind}")
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {side}")

        from src.brokers.client_id import generate  # noqa: PLC0415
        cid = generate(
            strategy="protect",
            symbol=symbol,
            side=side,
            ts_ms=self._client._now_ms(),
        )
        params = {
            "symbol": symbol,
            "side": side,
            "type": kind,
            "quantity": str(qty),
            "stopPrice": str(stop_price),
            "reduceOnly": "true",
            "timeInForce": "GTC",
            "workingType": "MARK_PRICE",  # mark price-triggered (less wick noise)
            "newClientOrderId": cid,
        }
        raw = self._client._post("/fapi/v1/order", params)
        order_id = raw.get("orderId")
        if order_id is None:
            raise RuntimeError(f"protective order missing orderId in response: {raw!r}")
        return str(order_id)

    def cancel_protective_order(self, *, symbol: str, broker_order_id: str) -> None:
        """Cancel a previously-registered protective order by broker_order_id."""
        self._client.cancel_order(symbol, broker_order_id=broker_order_id)

    def list_open_protective_orders(
        self,
        *,
        symbol: str | None = None,
    ) -> list[dict]:
        """Return open STOP_MARKET / TAKE_PROFIT_MARKET orders only.

        Used by ProtectiveOrderManager.sync_from_broker() on PC restart to
        reconstruct manager state vs exchange state.
        """
        params = {"symbol": symbol} if symbol else {}
        raw = self._client._get("/fapi/v1/openOrders", params)
        if not isinstance(raw, list):
            return []
        protective_types = {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
        out: list[dict] = []
        for o in raw:
            if o.get("type") in protective_types:
                out.append({
                    "broker_order_id": str(o.get("orderId")),
                    "symbol": o.get("symbol"),
                    "side": o.get("side"),
                    "type": o.get("type"),
                    "stop_price": o.get("stopPrice"),
                    "client_order_id": o.get("clientOrderId"),
                })
        return out
