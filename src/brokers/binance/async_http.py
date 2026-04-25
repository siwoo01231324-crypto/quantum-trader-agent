"""Async HMAC-signed HTTP client for Binance USDS-M Futures.

Uses httpx.AsyncClient with trust_env=False (R11).
Rate limiting via AsyncTokenBucket (wait semantics).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from src.brokers.async_rate_limiter import AsyncBinanceRateLimiter
from src.brokers.binance.error_map import map_error
from src.brokers.binance.schemas import (
    BalanceItem,
    CancelOrderResponse,
    GetOrderResponse,
    PlaceOrderResponse,
    PositionRisk,
)
from src.brokers.base import MarginType
from src.brokers.errors import TimestampError, ValidationError
from src.brokers.base import OrderRequest, PositionSide

log = logging.getLogger(__name__)

_TIME_SYNC_TTL_S = 900  # 15 minutes
_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)


def _sign(secret: bytes, params: dict[str, Any], timestamp_ms: int, recv_window: int) -> dict[str, Any]:
    """Pure function: add timestamp + recvWindow, compute HMAC-SHA256 signature."""
    p = dict(params)
    p["timestamp"] = timestamp_ms
    p["recvWindow"] = recv_window
    query = urlencode(p)
    sig = hmac.new(secret, query.encode(), hashlib.sha256).hexdigest()
    p["signature"] = sig
    return p


class AsyncBinanceFuturesClient:
    """Async low-level HMAC-signed HTTP client for Binance USDS-M Futures."""

    def __init__(
        self,
        api_key: str,
        secret: str,
        base_url: str,
        rate_limiter: AsyncBinanceRateLimiter,
        recv_window_ms: int = 5000,
    ) -> None:
        self._api_key = api_key
        self._secret = secret.encode()
        self._base_url = base_url.rstrip("/")
        self._rate_limiter = rate_limiter
        self._recv_window = recv_window_ms
        self._time_offset_ms: int = 0
        self._last_sync: float = 0.0
        self._client = httpx.AsyncClient(
            trust_env=False,
            limits=_LIMITS,
            headers={"X-MBX-APIKEY": api_key},
            timeout=10.0,
        )

    # ── time sync ────────────────────────────────────────────────────────────

    async def _sync_server_time(self) -> None:
        resp = await self._client.get(f"{self._base_url}/fapi/v1/time")
        resp.raise_for_status()
        server_ms: int = resp.json()["serverTime"]
        local_ms = int(time.time() * 1000)
        self._time_offset_ms = server_ms - local_ms
        self._last_sync = time.monotonic()
        log.debug("Time sync: offset=%dms", self._time_offset_ms)

    async def _ensure_time_sync(self) -> None:
        age = time.monotonic() - self._last_sync
        if age > _TIME_SYNC_TTL_S:
            await self._sync_server_time()

    def _now_ms(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    # ── request helpers ───────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        retry_on_timestamp: bool = True,
        signed: bool = True,
    ) -> Any:
        await self._ensure_time_sync()

        raw_params = dict(params or {})
        if signed:
            final_params = _sign(self._secret, raw_params, self._now_ms(), self._recv_window)
        else:
            final_params = raw_params

        if method == "GET":
            resp = await self._client.get(
                f"{self._base_url}{path}",
                params=final_params,
            )
        elif method == "POST":
            resp = await self._client.post(
                f"{self._base_url}{path}",
                data=final_params,
            )
        elif method == "PUT":
            resp = await self._client.put(
                f"{self._base_url}{path}",
                data=final_params,
            )
        elif method == "DELETE":
            resp = await self._client.delete(
                f"{self._base_url}{path}",
                params=final_params,
            )
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        self._rate_limiter.on_response_headers(dict(resp.headers))

        if not resp.is_success:
            payload: dict = {}
            try:
                payload = resp.json()
            except Exception:
                pass
            code = payload.get("code", 0)
            msg = payload.get("msg", resp.text)
            exc = map_error(int(code), msg)
            if isinstance(exc, TimestampError) and retry_on_timestamp:
                log.warning("Timestamp error — resyncing clock and retrying")
                await self._sync_server_time()
                return await self._request(method, path, params, retry_on_timestamp=False, signed=signed)
            raise exc

        return resp.json()

    async def _get(self, path: str, params: dict[str, Any] | None = None, signed: bool = True) -> Any:
        return await self._request("GET", path, params, signed=signed)

    async def _post(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("POST", path, params)

    async def _put(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("PUT", path, params)

    async def _delete(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("DELETE", path, params)

    # ── unsigned helpers ──────────────────────────────────────────────────────

    async def ping(self) -> None:
        await self._get("/fapi/v1/ping", signed=False)

    async def get_server_time(self) -> int:
        raw = await self._get("/fapi/v1/time", signed=False)
        return int(raw["serverTime"])

    # ── listen key ────────────────────────────────────────────────────────────

    async def issue_listen_key(self) -> str:
        raw = await self._post("/fapi/v1/listenKey", {})
        return raw["listenKey"]

    async def extend_listen_key(self, listen_key: str) -> None:
        await self._put("/fapi/v1/listenKey", {"listenKey": listen_key})

    async def delete_listen_key(self, listen_key: str) -> None:
        await self._delete("/fapi/v1/listenKey", {"listenKey": listen_key})

    # ── order APIs ───────────────────────────────────────────────────────────

    async def place_order(self, req: OrderRequest, client_order_id: str) -> PlaceOrderResponse:
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

        raw = await self._post("/fapi/v1/order", params)
        return PlaceOrderResponse.model_validate(raw)

    async def cancel_order(
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
        raw = await self._delete("/fapi/v1/order", params)
        return CancelOrderResponse.model_validate(raw)

    async def get_order(
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
        raw = await self._get("/fapi/v1/order", params)
        return GetOrderResponse.model_validate(raw)

    async def get_open_orders(self, symbol: str | None = None) -> list[GetOrderResponse]:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        raw = await self._get("/fapi/v1/openOrders", params)
        return [GetOrderResponse.model_validate(o) for o in raw]

    async def get_position_risk(self, symbol: str | None = None) -> list[PositionRisk]:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        raw = await self._get("/fapi/v2/positionRisk", params)
        return [PositionRisk.model_validate(p) for p in raw]

    async def get_balance(self) -> list[BalanceItem]:
        raw = await self._get("/fapi/v2/balance")
        return [BalanceItem.model_validate(b) for b in raw]

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        await self._post("/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})

    async def set_margin_type(self, symbol: str, margin_type: MarginType) -> None:
        await self._post(
            "/fapi/v1/marginType",
            {"symbol": symbol, "marginType": margin_type.value},
        )

    async def get_position_mode(self) -> bool:
        raw = await self._get("/fapi/v1/positionSide/dual")
        return bool(raw.get("dualSidePosition", False))

    async def set_position_mode(self, *, hedge: bool) -> None:
        await self._post(
            "/fapi/v1/positionSide/dual",
            {"dualSidePosition": "true" if hedge else "false"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()
