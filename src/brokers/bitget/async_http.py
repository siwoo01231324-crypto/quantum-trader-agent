"""Async HTTP client for Bitget v2 USDT-M Futures REST API.

Authentication:
  ACCESS-SIGN = base64( HMAC-SHA256( timestamp + method + requestPath + body ) )
  + ACCESS-KEY / ACCESS-TIMESTAMP / ACCESS-PASSPHRASE headers.
  Demo trading additionally requires ``paptrading: 1``.

Notes:
  - ``requestPath`` includes the leading slash AND query string when present.
  - Body is empty string for GET; JSON-encoded string for POST.
  - Timestamp is ms (string).
  - Bitget allows ±5s clock drift. On code "40010" (timestamp expired) we
    resync clock via ``/api/v2/public/time`` and retry once.
  - All non-success responses raise ``BrokerError`` via ``error_map``.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from decimal import Decimal
from typing import Any

import httpx

from src.brokers.binance.symbol_filters import SymbolFilters  # type: ignore  # noqa: F401  (kept for parity comment)
from src.brokers.bitget.error_map import map_error
from src.brokers.bitget.schemas import (
    AccountResponse,
    ContractResponse,
    OrderDetailResponse,
    PlaceOrderResponse,
    PositionResponse,
)
from src.brokers.errors import BrokerError, TimestampError

log = logging.getLogger(__name__)

REST_BASE_LIVE = "https://api.bitget.com"

# Bitget Demo Trading uses the same host AND the same productType (``USDT-FUTURES``)
# AND the same marginCoin (``USDT``) as live. The only routing diff is the
# ``paptrading: 1`` header. ``SUSDT-FUTURES`` is a separate sUSDT-collateral
# product, NOT the USDT-Futures demo variant — verified empirically 2026-06-04
# against Bitget Demo $5,000 USDT account (40778 with SUSDT, 00000 with USDT).
DEMO_PRODUCT_TYPE = "USDT-FUTURES"
LIVE_PRODUCT_TYPE = "USDT-FUTURES"


class AsyncBitgetFuturesClient:
    """Low-level async REST client. ``AsyncBitgetFuturesAdapter`` wraps this."""

    def __init__(
        self,
        *,
        api_key: str,
        secret: str,
        passphrase: str,
        base_url: str = REST_BASE_LIVE,
        paper: bool = True,
        request_timeout: float = 15.0,
    ) -> None:
        self._key = api_key
        self._secret = secret
        self._passphrase = passphrase
        self._base_url = base_url.rstrip("/")
        self._paper = paper
        self._product_type = DEMO_PRODUCT_TYPE if paper else LIVE_PRODUCT_TYPE
        self._client = httpx.AsyncClient(timeout=request_timeout)
        self._time_offset_ms: int = 0  # server - local

    # ── sign helpers ──────────────────────────────────────────────────────────

    def _now_ms(self) -> str:
        return str(int(time.time() * 1000) + self._time_offset_ms)

    def _sign(self, timestamp: str, method: str, request_path: str, body: str) -> str:
        prehash = f"{timestamp}{method.upper()}{request_path}{body}".encode()
        digest = hmac.new(self._secret.encode(), prehash, hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def _headers(self, timestamp: str, signature: str) -> dict[str, str]:
        h = {
            "ACCESS-KEY": self._key,
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self._passphrase,
            "Content-Type": "application/json",
            "locale": "en-US",
        }
        if self._paper:
            h["paptrading"] = "1"
        return h

    async def _sync_server_time(self) -> None:
        try:
            r = await self._client.get(f"{self._base_url}/api/v2/public/time", timeout=5.0)
            j = r.json()
            srv_ms = int(j["data"]["serverTime"])
            self._time_offset_ms = srv_ms - int(time.time() * 1000)
            log.info("bitget clock sync: offset=%dms", self._time_offset_ms)
        except Exception as exc:  # noqa: BLE001
            log.warning("bitget clock sync failed: %s", exc)

    # ── core request ──────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        signed: bool = True,
        retry_on_timestamp: bool = True,
    ) -> Any:
        # Build request_path (path + sorted query string for sign stability)
        qs = ""
        if params:
            # Bitget docs example uses inserted order — but practice shows any
            # consistent order works. We mirror dict insertion order.
            qs = "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        request_path = path + qs
        body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""

        if signed:
            ts = self._now_ms()
            sig = self._sign(ts, method, request_path, body_str)
            headers = self._headers(ts, sig)
        else:
            headers = {"Content-Type": "application/json"}

        url = f"{self._base_url}{request_path}"
        if method == "GET":
            resp = await self._client.get(url, headers=headers)
        elif method == "POST":
            resp = await self._client.post(url, headers=headers, content=body_str)
        elif method == "DELETE":
            resp = await self._client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        # Bitget always returns 200 + json with code/msg even for "errors".
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            log.warning("bitget %s %s non-json response: %s", method, path, resp.text[:200])
            raise BrokerError(f"non-json response status={resp.status_code}")

        code = str(data.get("code", "?"))
        if code == "00000":
            return data.get("data")

        msg = data.get("msg", "")
        log.warning(
            "bitget %s %s failed code=%s msg=%s params=%s",
            method, path, code, msg,
            {k: v for k, v in (params or {}).items()},
        )
        exc = map_error(code, msg)
        # Timestamp drift — resync once and retry.
        if isinstance(exc, TimestampError) and retry_on_timestamp:
            log.warning("bitget timestamp drift — resyncing clock and retrying")
            await self._sync_server_time()
            return await self._request(
                method, path, params=params, body=body,
                signed=signed, retry_on_timestamp=False,
            )
        raise exc

    # ── helpers ───────────────────────────────────────────────────────────────

    @property
    def product_type(self) -> str:
        return self._product_type

    async def ping(self) -> None:
        # Bitget public ping = /api/v2/public/time
        await self._request("GET", "/api/v2/public/time", signed=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── public market ────────────────────────────────────────────────────────

    async def get_contracts(self, *, symbol: str | None = None) -> list[ContractResponse]:
        params = {"productType": self._product_type}
        if symbol is not None:
            params["symbol"] = symbol
        data = await self._request(
            "GET", "/api/v2/mix/market/contracts",
            params=params, signed=False,
        )
        return [ContractResponse.from_json(d) for d in (data or [])]

    # ── account ──────────────────────────────────────────────────────────────

    async def get_account(self, *, symbol: str, margin_coin: str = "USDT") -> AccountResponse:
        data = await self._request(
            "GET", "/api/v2/mix/account/account",
            params={
                "productType": self._product_type,
                "symbol": symbol,
                "marginCoin": margin_coin,
            },
        )
        return AccountResponse.from_json(data)

    # ── positions ────────────────────────────────────────────────────────────

    async def get_all_positions(self, *, margin_coin: str = "USDT") -> list[PositionResponse]:
        data = await self._request(
            "GET", "/api/v2/mix/position/all-position",
            params={"productType": self._product_type, "marginCoin": margin_coin},
        )
        return [PositionResponse.from_json(d) for d in (data or [])]

    async def get_single_position(
        self, *, symbol: str, margin_coin: str = "USDT",
    ) -> list[PositionResponse]:
        data = await self._request(
            "GET", "/api/v2/mix/position/single-position",
            params={
                "productType": self._product_type,
                "symbol": symbol,
                "marginCoin": margin_coin,
            },
        )
        return [PositionResponse.from_json(d) for d in (data or [])]

    # ── orders ───────────────────────────────────────────────────────────────

    async def place_order(
        self,
        *,
        symbol: str,
        side: str,          # "buy" | "sell"
        order_type: str,    # "market" | "limit"
        size: Decimal,
        price: Decimal | None,
        client_oid: str,
        margin_coin: str = "USDT",
        margin_mode: str = "crossed",
        trade_side: str | None = None,  # hedge mode 시 "open"/"close"
        reduce_only: bool = False,
        preset_tp_price: Decimal | None = None,
        preset_sl_price: Decimal | None = None,
    ) -> PlaceOrderResponse:
        body: dict[str, Any] = {
            "symbol": symbol,
            "productType": self._product_type,
            "marginMode": margin_mode,
            "marginCoin": margin_coin,
            "size": str(size),
            "side": side,
            "orderType": order_type,
            "clientOid": client_oid,
        }
        if price is not None:
            body["price"] = str(price)
        if trade_side is not None:
            body["tradeSide"] = trade_side
        if reduce_only:
            body["reduceOnly"] = "YES"
        # 2026-06-08 — 진입과 함께 거는 거래소 네이티브 TP/SL 트리거 가격.
        # 체결 시 Bitget 이 서버측에서 익절/손절 plan 자동 생성 (holdSide 불필요).
        if preset_tp_price is not None:
            body["presetStopSurplusPrice"] = str(preset_tp_price)
        if preset_sl_price is not None:
            body["presetStopLossPrice"] = str(preset_sl_price)
        data = await self._request("POST", "/api/v2/mix/order/place-order", body=body)
        return PlaceOrderResponse.from_json(data)

    async def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_oid: str | None = None,
    ) -> None:
        if not order_id and not client_oid:
            raise ValueError("order_id or client_oid required")
        body: dict[str, Any] = {
            "symbol": symbol,
            "productType": self._product_type,
        }
        if order_id:
            body["orderId"] = order_id
        if client_oid:
            body["clientOid"] = client_oid
        await self._request("POST", "/api/v2/mix/order/cancel-order", body=body)

    # ── 거래소 네이티브 TP/SL plan order (2026-06-08) ────────────────────────────
    # 진입 직후 거래소에 익절/손절 trigger 를 직접 등록한다. synthetic(봇이
    # mark-price 보다가 청산)과 달리 Bitget 매칭엔진이 서버측에서 즉시 청산 →
    # WS 지연/봇 다운과 무관하게 빠르고 robust. triggerType=mark_price,
    # executePrice="0"(트리거 시 시장가 청산). planType: profit_plan(TP) /
    # loss_plan(SL). holdSide 는 *보호 대상 포지션* 방향(long/short).

    async def place_tpsl_order(
        self,
        *,
        symbol: str,
        plan_type: str,          # "profit_plan" (TP) | "loss_plan" (SL)
        trigger_price: Decimal,
        hold_side: str,          # "long" | "short" — 보호 대상 포지션 방향
        size: Decimal,
        client_oid: str,
        trigger_type: str = "mark_price",
        margin_coin: str = "USDT",
    ) -> str:
        """Bitget v2 TPSL plan order 제출 — broker orderId 반환."""
        body: dict[str, Any] = {
            "marginCoin": margin_coin,
            "productType": self._product_type,
            "symbol": symbol,
            "planType": plan_type,
            "triggerPrice": str(trigger_price),
            "triggerType": trigger_type,
            "executePrice": "0",   # 0 = 트리거 시 시장가 청산
            "holdSide": hold_side,
            "size": str(size),
            "clientOid": client_oid,
        }
        data = await self._request(
            "POST", "/api/v2/mix/order/place-tpsl-order", body=body
        )
        return str((data or {}).get("orderId") or "")

    async def cancel_tpsl_order(
        self,
        *,
        symbol: str,
        order_id: str,
        margin_coin: str = "USDT",
    ) -> None:
        """TPSL plan order 취소 (orderIdList 1건)."""
        body: dict[str, Any] = {
            "marginCoin": margin_coin,
            "productType": self._product_type,
            "symbol": symbol,
            "orderIdList": [{"orderId": order_id}],
        }
        await self._request("POST", "/api/v2/mix/order/cancel-plan-order", body=body)

    async def get_pending_tpsl_orders(
        self,
        *,
        symbol: str | None = None,
    ) -> list[dict]:
        """현재 거래소 측 살아있는 TPSL plan order 목록 (재기동 동기화용)."""
        params: dict[str, Any] = {
            "productType": self._product_type,
            "planType": "profit_loss",   # TP/SL plan 군
        }
        if symbol:
            params["symbol"] = symbol
        data = await self._request(
            "GET", "/api/v2/mix/order/orders-plan-pending", params=params
        )
        if isinstance(data, dict):
            rows = data.get("entrustedList") or data.get("list") or []
            return list(rows) if rows else []
        return list(data or [])

    async def get_order_detail(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_oid: str | None = None,
    ) -> OrderDetailResponse:
        if not order_id and not client_oid:
            raise ValueError("order_id or client_oid required")
        params: dict[str, Any] = {
            "symbol": symbol,
            "productType": self._product_type,
        }
        if order_id:
            params["orderId"] = order_id
        if client_oid:
            params["clientOid"] = client_oid
        data = await self._request("GET", "/api/v2/mix/order/detail", params=params)
        return OrderDetailResponse.from_json(data)

    # ── leverage / margin / position mode ─────────────────────────────────────

    async def set_leverage(
        self, *, symbol: str, leverage: int,
        margin_coin: str = "USDT", hold_side: str | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "symbol": symbol,
            "productType": self._product_type,
            "marginCoin": margin_coin,
            "leverage": str(leverage),
        }
        if hold_side is not None:
            body["holdSide"] = hold_side
        await self._request("POST", "/api/v2/mix/account/set-leverage", body=body)

    async def set_margin_mode(
        self, *, symbol: str, mode: str,  # "crossed" | "isolated"
        margin_coin: str = "USDT",
    ) -> None:
        await self._request(
            "POST", "/api/v2/mix/account/set-margin-mode",
            body={
                "symbol": symbol, "productType": self._product_type,
                "marginCoin": margin_coin, "marginMode": mode,
            },
        )

    async def set_position_mode(self, *, hedge: bool) -> None:
        # Bitget: posMode = "one_way_mode" | "hedge_mode"
        await self._request(
            "POST", "/api/v2/mix/account/set-position-mode",
            body={
                "productType": self._product_type,
                "posMode": "hedge_mode" if hedge else "one_way_mode",
            },
        )

    async def get_position_mode(self) -> str:
        # 별도 조회 API 가 없어 account 응답의 posMode 필드 fallback.
        # MVP: 기록 안 함. 호출자는 set_position_mode 만 사용.
        return "one_way_mode"
