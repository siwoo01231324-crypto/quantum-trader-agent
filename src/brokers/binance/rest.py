from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import requests

from src.brokers.base import Balance, MarginType, OrderAck, OrderRequest, Position, PositionSide
from src.brokers.binance.error_map import map_error
from src.brokers.binance.schemas import (
    BalanceItem,
    CancelOrderResponse,
    GetOrderResponse,
    PlaceOrderResponse,
    PositionRisk,
)
from src.brokers.errors import TimestampError, ValidationError
from src.brokers.rate_limiter import RateLimiter

log = logging.getLogger(__name__)

_TIME_SYNC_TTL_S = 900  # 15 minutes


class BinanceFuturesClient:
    """Low-level HMAC-signed HTTP client for Binance USDS-M Futures."""

    def __init__(
        self,
        api_key: str,
        secret: str,
        base_url: str,
        rate_limiter: RateLimiter,
        recv_window_ms: int = 5000,
    ) -> None:
        self._api_key = api_key
        self._secret = secret.encode()
        self._base_url = base_url.rstrip("/")
        self._rate_limiter = rate_limiter
        self._recv_window = recv_window_ms
        self._time_offset_ms: int = 0
        self._last_sync: float = 0.0
        self._session = requests.Session()
        self._session.headers.update({"X-MBX-APIKEY": api_key})

    # ── time sync ────────────────────────────────────────────────────────────

    def _sync_server_time(self) -> None:
        resp = self._session.get(f"{self._base_url}/fapi/v1/time", timeout=10)
        resp.raise_for_status()
        server_ms: int = resp.json()["serverTime"]
        local_ms = int(time.time() * 1000)
        self._time_offset_ms = server_ms - local_ms
        self._last_sync = time.monotonic()
        log.debug("Time sync: offset=%dms", self._time_offset_ms)

    def _ensure_time_sync(self) -> None:
        age = time.monotonic() - self._last_sync
        if age > _TIME_SYNC_TTL_S:
            self._sync_server_time()

    def _now_ms(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    # ── signing ──────────────────────────────────────────────────────────────

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        params["timestamp"] = self._now_ms()
        params["recvWindow"] = self._recv_window
        query = urlencode(params)
        sig = hmac.new(self._secret, query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    # ── request helpers ───────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        retry_on_timestamp: bool = True,
    ) -> Any:
        self._ensure_time_sync()
        signed = self._sign(dict(params or {}))

        resp = self._session.request(
            method,
            f"{self._base_url}{path}",
            params=signed if method == "GET" else None,
            data=signed if method != "GET" else None,
            timeout=10,
        )
        self._rate_limiter.on_response_headers(dict(resp.headers))

        if not resp.ok:
            payload = {}
            try:
                payload = resp.json()
            except Exception:
                pass
            code = payload.get("code", 0)
            msg = payload.get("msg", resp.text)
            exc = map_error(int(code), msg)
            if isinstance(exc, TimestampError) and retry_on_timestamp:
                log.warning("Timestamp error — resyncing clock and retrying")
                self._sync_server_time()
                return self._request(method, path, params, retry_on_timestamp=False)
            raise exc

        return resp.json()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params)

    def _post(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, params)

    def _delete(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("DELETE", path, params)

    # ── order APIs ───────────────────────────────────────────────────────────

    def place_order(self, req: OrderRequest, client_order_id: str) -> PlaceOrderResponse:
        if req.position_side != PositionSide.BOTH and req.reduce_only:
            raise ValidationError(
                "reduceOnly is not allowed in hedge mode (positionSide != BOTH)"
            )

        params: dict[str, Any] = {
            "symbol": req.symbol,
            "side": req.side.value,
            "type": req.order_type.value,
            "quantity": str(req.qty),
            "newClientOrderId": client_order_id,
            "positionSide": req.position_side.value,
        }
        if req.price is not None:
            params["price"] = str(req.price)
            params["timeInForce"] = req.tif.value
        if req.reduce_only:
            params["reduceOnly"] = "true"
        if req.close_position:
            params["closePosition"] = "true"

        raw = self._post("/fapi/v1/order", params)
        return PlaceOrderResponse.model_validate(raw)

    def cancel_order(
        self,
        symbol: str,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> CancelOrderResponse:
        params: dict[str, Any] = {"symbol": symbol}
        if broker_order_id is not None:
            params["orderId"] = broker_order_id
        elif client_order_id is not None:
            params["origClientOrderId"] = client_order_id
        else:
            raise ValueError("broker_order_id or client_order_id required")
        raw = self._delete("/fapi/v1/order", params)
        return CancelOrderResponse.model_validate(raw)

    def get_order(
        self,
        symbol: str,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> GetOrderResponse:
        params: dict[str, Any] = {"symbol": symbol}
        if broker_order_id is not None:
            params["orderId"] = broker_order_id
        elif client_order_id is not None:
            params["origClientOrderId"] = client_order_id
        else:
            raise ValueError("broker_order_id or client_order_id required")
        raw = self._get("/fapi/v1/order", params)
        return GetOrderResponse.model_validate(raw)

    def get_open_orders(self, symbol: str | None = None) -> list[GetOrderResponse]:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        raw = self._get("/fapi/v1/openOrders", params)
        return [GetOrderResponse.model_validate(o) for o in raw]

    def get_position_risk(self, symbol: str | None = None) -> list[PositionRisk]:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        raw = self._get("/fapi/v2/positionRisk", params)
        return [PositionRisk.model_validate(p) for p in raw]

    def get_balance(self) -> list[BalanceItem]:
        raw = self._get("/fapi/v2/balance")
        return [BalanceItem.model_validate(b) for b in raw]

    def set_leverage(self, symbol: str, leverage: int) -> None:
        self._post("/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})

    def set_margin_type(self, symbol: str, margin_type: MarginType) -> None:
        self._post(
            "/fapi/v1/marginType",
            {"symbol": symbol, "marginType": margin_type.value},
        )

    def get_position_mode(self) -> bool:
        """Return True if hedge mode (dualSidePosition=true), False if one-way."""
        raw = self._get("/fapi/v1/positionSide/dual")
        return bool(raw.get("dualSidePosition", False))

    def set_position_mode(self, *, hedge: bool) -> None:
        self._post(
            "/fapi/v1/positionSide/dual",
            {"dualSidePosition": "true" if hedge else "false"},
        )
