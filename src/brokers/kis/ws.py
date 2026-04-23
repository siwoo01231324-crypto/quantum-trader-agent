from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import websocket

from src.brokers.base import Closeable
from src.brokers.kis.auth import KISAuth
from src.brokers.kis.crypto import decrypt_aes256_cbc_pkcs7
from src.brokers.kis.tr_ids import tr_ids_for
from src.brokers.types import BrokerFill

log = logging.getLogger(__name__)

# KIS WS 엔드포인트 (출처: wikidocs.net/170517)
_PAPER_WS_URL = "ws://ops.koreainvestment.com:31000"
_LIVE_WS_URL = "ws://ops.koreainvestment.com:21000"

# 세션당 최대 구독 가능 종목 수 (출처: hky035.github.io/web/refact-kis-websocket/)
_MAX_SUBSCRIPTIONS = 41
# 체결통보는 세션당 1건
_MAX_EXECUTION_SUBSCRIPTIONS = 1


class _KISWSCloseable:
    def __init__(self, ws_app: websocket.WebSocketApp, thread: threading.Thread) -> None:
        self._ws = ws_app
        self._thread = thread

    def close(self) -> None:
        self._ws.close()
        self._thread.join(timeout=5)


class KISWebSocket:
    """KIS 체결통보 WebSocket 클라이언트.

    - AES-256-CBC + PKCS7 복호화 (key/iv 는 구독 응답에서 추출)
    - ^ 구분자 23 필드 파싱 → BrokerFill
    - paper: ws://ops.koreainvestment.com:31000, TR H0STCNI9
    - live:  ws://ops.koreainvestment.com:21000, TR H0STCNI0
    - 세션당 종목 41개 + 체결통보 1건 한도
    """

    def __init__(
        self,
        auth: KISAuth,
        app_key: str,
        hts_id: str,
        paper: bool = True,
        on_fill: Callable[[BrokerFill], None] | None = None,
    ) -> None:
        self._auth = auth
        self._app_key = app_key
        self._hts_id = hts_id
        self._paper = paper
        self._on_fill = on_fill
        self._tr_ids = tr_ids_for(paper)
        self._ws_url = _PAPER_WS_URL if paper else _LIVE_WS_URL
        self._tr_id = self._tr_ids["ws_execution"]

        self._aes_key: str | None = None
        self._aes_iv: str | None = None
        self._subscription_count = 0
        self._execution_subscribed = False

    def _get_approval_key(self) -> str:
        """WS 접속키 발급 (approval_key)."""
        import requests
        base = (
            "https://openapivts.koreainvestment.com:29443"
            if self._paper
            else "https://openapi.koreainvestment.com:9443"
        )
        resp = requests.post(
            f"{base}/oauth2/Approval",
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "secretkey": self._auth._app_secret,
            },
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["approval_key"]

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
                    "tr_key": self._hts_id,  # HTS ID (종목코드 아님)
                }
            },
        })

    def _on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        try:
            self._handle_message(message)
        except Exception as exc:
            log.warning("KIS WS message handling error: %s", exc)

    def _handle_message(self, message: str) -> None:
        # 구독 응답 (JSON)
        if message.startswith("{"):
            data = json.loads(message)
            header = data.get("header", {})
            body = data.get("body", {})

            if header.get("tr_id") == self._tr_id and body.get("rt_cd") == "0":
                output = body.get("output", {})
                self._aes_key = output.get("key")
                self._aes_iv = output.get("iv")
                if self._aes_key and self._aes_iv:
                    log.info("KIS WS: AES key/iv received for decryption")
                    self._execution_subscribed = True
                else:
                    log.warning("KIS WS: subscription OK but no AES key/iv")
            return

        # 데이터 메시지: "0|TR_ID|count|payload" 형식
        # 암호화된 체결통보: pipe 구분
        parts = message.split("|")
        if len(parts) < 4:
            return

        enc_flag = parts[0]   # "0"=비암호화, "1"=암호화
        tr_id = parts[1]
        # parts[2] = count
        payload = parts[3]

        if tr_id not in (self._tr_id,):
            return

        if enc_flag == "1":
            if not self._aes_key or not self._aes_iv:
                log.warning("KIS WS: encrypted message received but no AES key/iv")
                return
            try:
                payload = decrypt_aes256_cbc_pkcs7(payload, self._aes_key, self._aes_iv)
            except Exception as exc:
                log.warning("KIS WS: AES decryption failed: %s", exc)
                return

        fill = self._parse_execution(payload)
        if fill and self._on_fill:
            self._on_fill(fill)

    def _parse_execution(self, payload: str) -> BrokerFill | None:
        """^ 구분자 23 필드 파싱 → BrokerFill.

        필드 순서 (출처: wikidocs.net/164065):
        0:고객ID, 1:계좌번호, 2:주문번호, 3:원주문번호,
        4:매도매수구분, 5:정정구분, 6:주문종류, 7:주문조건,
        8:종목코드, 9:체결수량, 10:체결단가, 11:체결시각,
        12:거부여부, 13:체결여부, 14:접수여부, 15:지점번호,
        16:주문수량, 17:계좌명, 18:체결종목명, 19:신용구분,
        20:신용대출일자, 21:체결종목명40, 22:주문단가
        """
        fields = payload.split("^")
        if len(fields) < 23:
            log.warning("KIS WS: expected 23 fields, got %d", len(fields))
            return None

        # 체결여부 필드(13): "2"=체결, "1"=접수, "0"=거부
        exec_flag = fields[13]
        if exec_flag != "2":
            return None  # 체결이 아닌 이벤트는 무시

        try:
            qty = Decimal(fields[9]) if fields[9] else Decimal("0")
            price = Decimal(fields[10]) if fields[10] else Decimal("0")
            symbol = fields[8]
            order_no = fields[2]
            ts_str = fields[11]  # HHMMSS

            now = datetime.now(tz=timezone.utc)
            ts = now  # 체결시각(HHMMSS)은 날짜 없으므로 현재 날짜 기준

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
            log.warning("KIS WS: fill parse error: %s (payload=%r)", exc, payload)
            return None

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        log.error("KIS WS error: %s", error)

    def _on_close(self, ws: websocket.WebSocketApp, close_status_code, close_msg) -> None:
        log.info("KIS WS closed: %s %s", close_status_code, close_msg)

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        log.info("KIS WS connected to %s", self._ws_url)
        if self._execution_subscribed:
            log.warning("KIS WS: 체결통보는 세션당 1건 한도 (이미 구독됨)")
            return
        try:
            approval_key = self._get_approval_key()
            ws.send(self._subscribe_msg(approval_key))
            self._subscription_count += 1
            if self._subscription_count > _MAX_SUBSCRIPTIONS:
                log.warning(
                    "KIS WS: 세션당 종목 %d개 한도 초과 (%d개 구독 시도)",
                    _MAX_SUBSCRIPTIONS,
                    self._subscription_count,
                )
        except Exception as exc:
            log.error("KIS WS subscription failed: %s", exc)

    def connect(self) -> Closeable:
        """WS 연결을 백그라운드 스레드로 시작하고 Closeable 반환."""
        ws_app = websocket.WebSocketApp(
            self._ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        thread = threading.Thread(
            target=ws_app.run_forever,
            kwargs={"reconnect": 5},
            daemon=True,
        )
        thread.start()
        return _KISWSCloseable(ws_app, thread)
