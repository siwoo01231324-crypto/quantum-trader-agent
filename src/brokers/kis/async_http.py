"""KIS async REST client (httpx.AsyncClient 기반).

async 전용. sync requests / httpx.Client 혼재 절대 금지.
재시도 정책: 5xx 및 네트워크 오류에만 exponential backoff.
4xx / rt_cd 비즈니스 오류는 즉시 raise.
"""
from __future__ import annotations

import logging
from decimal import Decimal

import httpx

from src.brokers.base import Balance, OrderAck, OrderType, Position, PositionSide
from src.brokers.errors import BrokerError
from src.brokers.kis.auth import KISAuth
from src.brokers.kis.error_map import map_error
from src.brokers.kis.schemas import (
    KISBalanceResponse,
    KISBuyableResponse,
    KISOrderResponse,
)
from src.brokers.kis.tr_ids import tr_ids_for
from src.execution.base import Side

log = logging.getLogger(__name__)

_RETRY_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.4  # doubles each retry: 0.4, 0.8, 1.6s


class KISAsyncClient:
    """KIS REST API async 클라이언트 (httpx.AsyncClient 기반)."""

    def __init__(
        self,
        auth: KISAuth,
        app_key: str,
        app_secret: str,
        cano: str,
        acnt_prdt_cd: str,
        paper: bool = True,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._auth = auth
        self._app_key = app_key
        self._app_secret = app_secret
        self._cano = cano
        self._acnt_prdt_cd = acnt_prdt_cd
        self._paper = paper
        self._tr_ids = tr_ids_for(paper)

        if paper:
            self._base_url = "https://openapivts.koreainvestment.com:29443"
        else:
            self._base_url = "https://openapi.koreainvestment.com:9443"

        # 외부에서 주입하거나 내부 생성 (테스트 시 respx mock 주입)
        self._http = http_client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=10.0,
            trust_env=False,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
            ),
        )
        self._owns_http = http_client is None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _headers(self, tr_id: str) -> dict[str, str]:
        token = await self._auth.get_token_async()
        return {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _check_response(self, data: dict) -> None:
        if data.get("rt_cd") == "1":
            raise map_error(data.get("msg_cd", ""), data.get("msg1", ""))

    async def _request_with_retry(
        self, method: str, path: str, tr_id: str, **kwargs
    ) -> dict:
        """5xx / 네트워크 오류에 exponential backoff 재시도. 4xx 즉시 raise."""
        import asyncio

        last_exc: Exception | None = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                headers = await self._headers(tr_id)
                resp = await self._http.request(
                    method, path, headers=headers, **kwargs
                )
                if 500 <= resp.status_code < 600:
                    resp.raise_for_status()
                resp.raise_for_status()
                data = resp.json()
                self._check_response(data)
                return data
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if 500 <= status < 600 and attempt < _RETRY_MAX_ATTEMPTS - 1:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning(
                        "KIS %s %s returned %d (attempt %d/%d), retrying in %.2fs",
                        method, path, status, attempt + 1, _RETRY_MAX_ATTEMPTS, delay,
                    )
                    await asyncio.sleep(delay)
                    last_exc = exc
                    continue
                raise
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                if attempt < _RETRY_MAX_ATTEMPTS - 1:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning(
                        "KIS %s %s network error (%s), retrying in %.2fs",
                        method, path, type(exc).__name__, delay,
                    )
                    await asyncio.sleep(delay)
                    last_exc = exc
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    async def _post(self, path: str, tr_id: str, body: dict) -> dict:
        return await self._request_with_retry("POST", path, tr_id, json=body)

    async def _get(self, path: str, tr_id: str, params: dict) -> dict:
        return await self._request_with_retry("GET", path, tr_id, params=params)

    # ------------------------------------------------------------------
    # Order
    # ------------------------------------------------------------------

    async def place_order(
        self,
        symbol: str,
        side: Side,
        order_type: OrderType,
        qty: Decimal,
        price: Decimal | None,
    ) -> KISOrderResponse:
        tr_id = (
            self._tr_ids["order_buy"]
            if side == Side.BUY
            else self._tr_ids["order_sell"]
        )
        ord_dvsn = "01" if order_type == OrderType.MARKET else "00"
        ord_unpr = "0" if order_type == OrderType.MARKET else str(int(price))

        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "PDNO": symbol,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(int(qty)),
            "ORD_UNPR": ord_unpr,
        }

        data = await self._post(
            "/uapi/domestic-stock/v1/trading/order-cash", tr_id, body
        )
        return KISOrderResponse.model_validate(data)

    async def cancel_order(
        self,
        broker_order_id: str,
        symbol: str,
        qty: int,
        price: int,
    ) -> KISOrderResponse:
        tr_id = self._tr_ids["order_modify_cancel"]
        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": broker_order_id,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
            "QTY_ALL_ORD_YN": "Y",
            "PDNO": symbol,
        }
        data = await self._post(
            "/uapi/domestic-stock/v1/trading/order-cash", tr_id, body
        )
        return KISOrderResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def get_balance(self) -> KISBalanceResponse:
        tr_id = self._tr_ids["balance"]
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "N",
            "INQR_DVSN": "01",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        data = await self._get(
            "/uapi/domestic-stock/v1/trading/inquire-balance", tr_id, params
        )
        return KISBalanceResponse.model_validate(data)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
