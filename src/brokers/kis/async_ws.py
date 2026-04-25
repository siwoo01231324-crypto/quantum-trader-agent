"""KIS async WebSocket 클라이언트 (websockets>=12 기반).

async 전용. threading / websocket-client / time.sleep 절대 금지.
approval_key: WS 구독 직전 1회 발급 (sync ws.py:72-91 패턴 유지).
background keepalive task 도입 금지 (플랜 Must NOT).
체결통보 AES-256-CBC 복호화: crypto.py 재사용.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

import httpx
import websockets
import websockets.exceptions

from src.brokers.errors import WSDisconnectedError
from src.brokers.kis.auth import KISAuth
from src.brokers.kis.crypto import decrypt_aes256_cbc_pkcs7
from src.brokers.kis.tr_ids import tr_ids_for
from src.brokers.types import BrokerFill

log = logging.getLogger(__name__)

_PAPER_WS_URL = "ws://ops.koreainvestment.com:31000"
_LIVE_WS_URL = "ws://ops.koreainvestment.com:21000"

_PAPER_REST_BASE = "https://openapivts.koreainvestment.com:29443"
_LIVE_REST_BASE = "https://openapi.koreainvestment.com:9443"

# 재접속 backoff 설정
_RECONNECT_BASE_DELAY = 1.0
_RECONNECT_MAX_DELAY = 10.0
_RECONNECT_JITTER = 0.2


class KISAsyncWebSocket:
    """KIS 체결통보 async WebSocket — AsyncIterator[BrokerFill] 제공.

    Usage:
        async for fill in ws.stream_fills():
            ...
    """

    def __init__(
        self,
        auth: KISAuth,
        app_key: str,
        hts_id: str,
        paper: bool = True,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._auth = auth
        self._app_key = app_key
        self._hts_id = hts_id
        self._paper = paper
        self._tr_ids = tr_ids_for(paper)
        self._ws_url = _PAPER_WS_URL if paper else _LIVE_WS_URL
        self._rest_base = _PAPER_REST_BASE if paper else _LIVE_REST_BASE
        self._tr_id = self._tr_ids["ws_execution"]

        self._http = http_client or httpx.AsyncClient(
            timeout=10.0,
            trust_env=False,
        )
        self._owns_http = http_client is None

        self._aes_key: str | None = None
        self._aes_iv: str | None = None

        self._closing = False

    # ------------------------------------------------------------------
    # approval_key 발급 (WS 구독 직전 1회)
    # ------------------------------------------------------------------

    async def _get_approval_key(self) -> str:
        """WS approval_key 발급 — sync ws.py:72-91 패턴을 async 로 유지."""
        resp = await self._http.post(
            f"{self._rest_base}/oauth2/Approval",
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "secretkey": self._auth._app_secret,
            },
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()["approval_key"]

    # ------------------------------------------------------------------
    # 구독 메시지
    # ------------------------------------------------------------------

    def _subscribe_msg(self, approval_key: str) -> str:
        return json.dumps({
            "header": {
                "approval_key": approval_key,
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": self._tr_id,
                    "tr_key": self._hts_id,
                }
            },
        })

    # ------------------------------------------------------------------
    # 메시지 파싱
    # ------------------------------------------------------------------

    def _handle_message(self, message: str) -> BrokerFill | None:
        if message.startswith("{"):
            data = json.loads(message)
            header = data.get("header", {})
            body = data.get("body", {})
            if header.get("tr_id") == self._tr_id and body.get("rt_cd") == "0":
                output = body.get("output", {})
                self._aes_key = output.get("key")
                self._aes_iv = output.get("iv")
                if self._aes_key and self._aes_iv:
                    log.info("KIS async WS: AES key/iv received")
                else:
                    log.warning("KIS async WS: subscription OK but no AES key/iv")
            return None

        parts = message.split("|")
        if len(parts) < 4:
            return None

        enc_flag = parts[0]
        tr_id = parts[1]
        payload = parts[3]

        if tr_id != self._tr_id:
            return None

        if enc_flag == "1":
            if not self._aes_key or not self._aes_iv:
                log.warning("KIS async WS: encrypted msg but no AES key/iv")
                return None
            try:
                payload = decrypt_aes256_cbc_pkcs7(payload, self._aes_key, self._aes_iv)
            except Exception as exc:
                log.warning("KIS async WS: AES decryption failed: %s", exc)
                return None

        return self._parse_execution(payload)

    def _parse_execution(self, payload: str) -> BrokerFill | None:
        fields = payload.split("^")
        if len(fields) < 23:
            log.warning("KIS async WS: expected 23 fields, got %d", len(fields))
            return None

        exec_flag = fields[13]
        if exec_flag != "2":
            return None

        try:
            qty = Decimal(fields[9]) if fields[9] else Decimal("0")
            price = Decimal(fields[10]) if fields[10] else Decimal("0")
            symbol = fields[8]
            order_no = fields[2]
            ts_str = fields[11]

            ts = datetime.now(tz=timezone.utc)

            return BrokerFill(
                parent_id="",
                broker_order_id=order_no,
                client_order_id="",
                trade_id=f"{order_no}:{ts_str}",
                qty=qty,
                price=price,
                fee=Decimal("0"),
                fee_asset="KRW",
                ts=ts,
                is_maker=False,
            )
        except Exception as exc:
            log.warning("KIS async WS: fill parse error: %s (payload=%r)", exc, payload)
            return None

    # ------------------------------------------------------------------
    # 재접속 backoff
    # ------------------------------------------------------------------

    def _backoff_delay(self, attempt: int) -> float:
        import random
        delay = min(_RECONNECT_BASE_DELAY * (2 ** attempt), _RECONNECT_MAX_DELAY)
        jitter = delay * _RECONNECT_JITTER * (2 * random.random() - 1)
        return max(0.0, delay + jitter)

    # ------------------------------------------------------------------
    # AsyncIterator[BrokerFill]
    # ------------------------------------------------------------------

    async def stream_fills(self) -> AsyncIterator[BrokerFill]:
        """체결통보 스트림. 연결 끊김 시 backoff 후 재접속."""
        attempt = 0
        while not self._closing:
            try:
                approval_key = await self._get_approval_key()
                async with websockets.connect(self._ws_url) as ws:
                    await ws.send(self._subscribe_msg(approval_key))
                    attempt = 0  # 연결 성공 시 리셋
                    async for raw in ws:
                        if self._closing:
                            return
                        try:
                            fill = self._handle_message(raw)
                        except Exception as exc:
                            log.warning("KIS async WS: message error: %s", exc)
                            continue
                        if fill is not None:
                            yield fill
            except websockets.exceptions.ConnectionClosed as exc:
                if self._closing:
                    return
                log.warning("KIS async WS: connection closed (%s), reconnecting...", exc)
            except Exception as exc:
                if self._closing:
                    return
                log.error("KIS async WS: unexpected error: %s", exc)
                raise WSDisconnectedError(str(exc)) from exc

            delay = self._backoff_delay(attempt)
            attempt += 1
            log.info("KIS async WS: reconnecting in %.2fs (attempt %d)", delay, attempt)
            await asyncio.sleep(delay)

    async def aclose(self) -> None:
        self._closing = True
        if self._owns_http:
            await self._http.aclose()
