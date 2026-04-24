from __future__ import annotations

import logging
import time
from decimal import Decimal

import requests

# KIS paper server (openapivts) occasionally returns 5xx under burst load
# (rate limit: paper 2 req/sec, live 20 req/sec). Retry with exponential backoff
# for transient 5xx only; 4xx / rt_cd business errors bubble up immediately.
_RETRY_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.4  # seconds, doubles each retry → 0.4, 0.8, 1.6

from src.brokers.base import Balance, OrderAck, OrderType, PositionSide, Position
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


class KISClient:
    """KIS REST API 클라이언트 (sync, requests 기반)."""

    def __init__(
        self,
        auth: KISAuth,
        app_key: str,
        app_secret: str,
        cano: str,
        acnt_prdt_cd: str,
        paper: bool = True,
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _headers(self, tr_id: str) -> dict[str, str]:
        token = self._auth.get_token()
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

    def _request_with_retry(self, method: str, url: str, tr_id: str, **kwargs) -> dict:
        """HTTP request with exponential-backoff retry on transient 5xx.

        Retries only 5xx (server errors). 4xx errors and rt_cd business errors
        are raised immediately — no sense retrying a malformed request.
        """
        last_exc: Exception | None = None
        for attempt in range(_RETRY_MAX_ATTEMPTS):
            try:
                resp = requests.request(
                    method,
                    url,
                    headers=self._headers(tr_id),
                    timeout=10,
                    **kwargs,
                )
                if 500 <= resp.status_code < 600:
                    resp.raise_for_status()
                resp.raise_for_status()
                data = resp.json()
                self._check_response(data)
                return data
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if 500 <= status < 600 and attempt < _RETRY_MAX_ATTEMPTS - 1:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning(
                        "KIS %s %s returned %d (attempt %d/%d), retrying in %.2fs",
                        method, url, status, attempt + 1, _RETRY_MAX_ATTEMPTS, delay,
                    )
                    time.sleep(delay)
                    last_exc = exc
                    continue
                raise
            except requests.RequestException as exc:
                # Network / timeout errors are also transient — retry
                if attempt < _RETRY_MAX_ATTEMPTS - 1:
                    delay = _RETRY_BASE_DELAY * (2 ** attempt)
                    log.warning(
                        "KIS %s %s network error (%s), retrying in %.2fs",
                        method, url, type(exc).__name__, delay,
                    )
                    time.sleep(delay)
                    last_exc = exc
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    def _post(self, path: str, tr_id: str, body: dict) -> dict:
        url = f"{self._base_url}{path}"
        return self._request_with_retry("POST", url, tr_id, json=body)

    def _get(self, path: str, tr_id: str, params: dict) -> dict:
        url = f"{self._base_url}{path}"
        return self._request_with_retry("GET", url, tr_id, params=params)

    def get_hashkey(self, body: dict) -> str | None:
        """hashkey 생성 (비필수 — 실패해도 주문 가능)."""
        try:
            url = f"{self._base_url}/uapi/hashkey"
            token = self._auth.get_token()
            headers = {
                "Content-Type": "application/json",
                "appKey": self._app_key,
                "appSecret": self._app_secret,
            }
            resp = requests.post(url, json=body, headers=headers, timeout=5)
            resp.raise_for_status()
            return resp.json().get("HASH")
        except Exception as exc:
            log.warning("hashkey 생성 실패 (비필수): %s", exc)
            return None

    # ------------------------------------------------------------------
    # Order
    # ------------------------------------------------------------------

    def place_order(
        self,
        symbol: str,
        side: Side,
        order_type: OrderType,
        qty: Decimal,
        price: Decimal | None,
        use_hashkey: bool = False,
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

        if use_hashkey:
            hk = self.get_hashkey(body)
            if hk:
                body["hashkey"] = hk

        data = self._post(
            "/uapi/domestic-stock/v1/trading/order-cash", tr_id, body
        )
        return KISOrderResponse.model_validate(data)

    def cancel_order(
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
            "RVSE_CNCL_DVSN_CD": "02",  # 02=취소
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
            "QTY_ALL_ORD_YN": "Y",
            "PDNO": symbol,
        }
        data = self._post(
            "/uapi/domestic-stock/v1/trading/order-cash", tr_id, body
        )
        return KISOrderResponse.model_validate(data)

    def modify_order(
        self,
        broker_order_id: str,
        symbol: str,
        qty: int,
        new_price: int,
    ) -> KISOrderResponse:
        tr_id = self._tr_ids["order_modify_cancel"]
        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": broker_order_id,
            "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "01",  # 01=정정
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(new_price),
            "QTY_ALL_ORD_YN": "N",
            "PDNO": symbol,
        }
        data = self._post(
            "/uapi/domestic-stock/v1/trading/order-cash", tr_id, body
        )
        return KISOrderResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_balance(self) -> KISBalanceResponse:
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
        data = self._get(
            "/uapi/domestic-stock/v1/trading/inquire-balance", tr_id, params
        )
        return KISBalanceResponse.model_validate(data)

    def get_buyable(self, symbol: str, price: int) -> KISBuyableResponse:
        tr_id = self._tr_ids["buyable"]
        params = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._acnt_prdt_cd,
            "PDNO": symbol,
            "ORD_UNPR": str(price),
            "ORD_DVSN": "00",
            "CMA_EVLU_AMT_ICLD_YN": "Y",
            "OVRS_ICLD_YN": "N",
        }
        data = self._get(
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order", tr_id, params
        )
        return KISBuyableResponse.model_validate(data)
