from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import websocket  # websocket-client

from src.brokers.binance.rest import BinanceFuturesClient
from src.brokers.binance.reconciler import ReconnectReconciler
from src.brokers.types import BrokerFill

log = logging.getLogger(__name__)

_KEEPALIVE_INTERVAL_S = 30 * 60   # 30 min
_RECONNECT_BEFORE_24H_S = 23 * 60 * 60  # 23h — reconnect before forced 24h disconnect


class BinanceUserDataStream:
    """Manages Binance USDS-M Futures user data WebSocket stream.

    Lifecycle:
      - POST /fapi/v1/listenKey to obtain a key (60 min TTL)
      - PUT  /fapi/v1/listenKey every 30 min to extend
      - Reconnect proactively at 23h (before 24h forced disconnect)
      - On reconnect: REST reconcile via ReconnectReconciler
    """

    def __init__(
        self,
        client: BinanceFuturesClient,
        ws_base_url: str,
        on_fill: Callable[[BrokerFill], None],
        reconciler: ReconnectReconciler,
    ) -> None:
        self._client = client
        self._ws_base_url = ws_base_url.rstrip("/")
        self._on_fill = on_fill
        self._reconciler = reconciler

        self._listen_key: str | None = None
        self._ws: websocket.WebSocketApp | None = None
        self._stopped = threading.Event()
        self._keepalive_thread: threading.Thread | None = None
        self._ws_thread: threading.Thread | None = None
        self._connected_at: float = 0.0

    # ── public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stopped.clear()
        self._listen_key = self._issue_listen_key()
        self._connected_at = time.monotonic()
        self._start_ws()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop, daemon=True, name="binance-ws-keepalive"
        )
        self._keepalive_thread.start()

    def close(self) -> None:
        self._stopped.set()
        if self._ws:
            self._ws.close()

    # ── listen key management ─────────────────────────────────────────────────

    def _issue_listen_key(self) -> str:
        raw = self._client._post("/fapi/v1/listenKey", {})
        key = raw["listenKey"]
        log.info("listenKey issued: %s", key[:8] + "...")
        return key

    def _extend_listen_key(self) -> None:
        assert self._listen_key
        self._client._request("PUT", "/fapi/v1/listenKey", {"listenKey": self._listen_key})
        log.debug("listenKey extended")

    def _reissue_listen_key(self) -> None:
        try:
            self._listen_key = self._issue_listen_key()
        except Exception as exc:
            log.error("Failed to reissue listenKey: %s", exc)

    # ── keepalive loop (runs in background thread) ────────────────────────────

    def _keepalive_loop(self) -> None:
        while not self._stopped.wait(timeout=60):
            age = time.monotonic() - self._connected_at
            # Proactive reconnect before 24h forced disconnect
            if age >= _RECONNECT_BEFORE_24H_S:
                log.info("Proactive WS reconnect at %dh", age // 3600)
                self._reconnect()
                continue
            # Extend listenKey every 30 min
            intervals_elapsed = int(age // _KEEPALIVE_INTERVAL_S)
            if intervals_elapsed > 0 and age % _KEEPALIVE_INTERVAL_S < 65:
                try:
                    self._extend_listen_key()
                except Exception:
                    log.warning("listenKey extend failed — reissuing")
                    self._reissue_listen_key()

    def _reconnect(self) -> None:
        if self._ws:
            self._ws.close()
        self._reissue_listen_key()
        self._connected_at = time.monotonic()
        self._start_ws()
        self._reconciler.reconcile()

    # ── websocket ─────────────────────────────────────────────────────────────

    def _start_ws(self) -> None:
        url = f"{self._ws_base_url}/{self._listen_key}"
        self._ws = websocket.WebSocketApp(
            url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws_thread = threading.Thread(
            target=self._ws.run_forever,
            daemon=True,
            name="binance-ws-stream",
        )
        self._ws_thread.start()

    def _on_message(self, ws: websocket.WebSocketApp, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            log.warning("Unparseable WS message: %r", raw[:200])
            return

        event_type = msg.get("e")
        if event_type == "ORDER_TRADE_UPDATE":
            self._handle_order_trade_update(msg)

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        log.error("WS error: %s", error)

    def _on_close(
        self,
        ws: websocket.WebSocketApp,
        close_status_code: int | None,
        close_msg: str | None,
    ) -> None:
        if not self._stopped.is_set():
            log.warning("WS closed (code=%s) — scheduling reconnect", close_status_code)
            threading.Thread(target=self._reconnect, daemon=True).start()

    # ── event handling ────────────────────────────────────────────────────────

    def _handle_order_trade_update(self, msg: dict) -> None:
        o = msg.get("o", {})
        exec_type = o.get("x")  # NEW, TRADE, CANCELED, EXPIRED, etc.
        if exec_type != "TRADE":
            return

        fill = self._reconciler.on_trade_event(o)
        if fill is not None:
            self._on_fill(fill)
