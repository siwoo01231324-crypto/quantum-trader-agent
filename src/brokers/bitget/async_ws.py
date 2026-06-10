"""Bitget v2 private WebSocket — user-data stream (orders channel).

Parallels ``src/brokers/binance/async_ws.py`` but uses Bitget's ``op:login``
auth flow (no listen-key) and the v2 ``orders`` channel for fill events.

WS endpoints:
  - Demo (paper): ``wss://wspap.bitget.com/v2/ws/private`` — discovered 2026-06-04
    by 30017 ("Current environment does not match the API Key") rejection on
    the live URL with a demo API key.
  - Live:         ``wss://ws.bitget.com/v2/ws/private``

Auth (sent immediately after connect):
  {"op":"login","args":[{"apiKey":..., "passphrase":..., "timestamp":<sec>,
                         "sign": base64(HMAC-SHA256(timestamp+"GET/user/verify"))}]}

Subscribe:
  {"op":"subscribe","args":[{"instType":"USDT-FUTURES","channel":"orders",
                             "instId":"default"}]}

Fill detection:
  v2 orders channel pushes one event per status change. We emit BrokerFill when
  ``status`` ∈ {"partially_filled", "filled"} AND the (orderId, tradeId) pair
  hasn't been seen. ``tradeId`` is unique per match; reconnect-snapshot uses
  same IDs so the dedup set is the recovery mechanism (matches Binance pattern).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import websockets
import websockets.exceptions

from src.brokers.async_backoff import exponential_backoff
from src.brokers.errors import WSConfigError, WSDisconnectedError
from src.brokers.types import BrokerFill

log = logging.getLogger(__name__)

OverflowPolicy = Literal["block", "drop_oldest", "raise"]

_DEFAULT_QUEUE_SIZE = int(os.environ.get("BROKER_FILL_QUEUE_SIZE", "1000"))
_RECONNECT_MAX_ATTEMPTS = 20
_PING_INTERVAL_SEC = 20.0

WS_PRIVATE_LIVE = "wss://ws.bitget.com/v2/ws/private"
WS_PRIVATE_DEMO = "wss://wspap.bitget.com/v2/ws/private"


def _parse_fill_from_order(
    o: dict,
    seen: set[tuple[str, str]],
    acc: dict[str, Decimal] | None = None,
) -> BrokerFill | None:
    """Translate one ``orders`` channel data row to a BrokerFill.

    Returns None when:
      - status is not a fill event (``live`` / ``canceled``).
      - (orderId, tradeId) already seen.
    """
    status = str(o.get("status", "")).lower()
    if status not in {"partially_filled", "filled"}:
        return None

    broker_order_id = str(o.get("orderId", ""))
    trade_id = str(o.get("tradeId", ""))
    # In rare cases Bitget pushes "filled" without tradeId on the final aggregate
    # — fall back to (orderId, "" + sequence) which still dedupes the final event.
    if not trade_id:
        trade_id = f"final-{o.get('uTime', '0')}"
    dedup_key = (broker_order_id, trade_id)
    if dedup_key in seen:
        return None
    seen.add(dedup_key)

    client_order_id = str(o.get("clientOid", ""))
    ts_ms = int(o.get("uTime") or o.get("fillTime") or o.get("cTime") or 0)
    ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) if ts_ms else datetime.now(tz=timezone.utc)

    # 2026-06-08 — Bitget orders 채널은 fill 마다 *누적* 체결량(accBaseVolume /
    # baseVolume)을 push 한다 (per-fill 증분이 아님). 따라서 push 값을 그대로
    # 기록하면 partial fill N 회 → N 배 부풀려진다 (BSBUSDT 8× 사고). 누적의
    # *증분*(이번 push 누적 − 직전 누적)만 이 fill 의 qty 로 기록한다. acc 미전달
    # (레거시/단위테스트) 시엔 단일 fill 가정으로 옛 동작 보존.
    cum = Decimal(str(
        o.get("accBaseVolume") or o.get("baseVolume")
        or o.get("fillSize") or o.get("size") or "0"
    ))
    if acc is not None:
        prev = acc.get(broker_order_id, Decimal("0"))
        qty = cum - prev
        # 누적이 안 늘었으면(증분 0 이하) 이 push 엔 신규 체결 없음 → skip.
        if qty <= 0:
            return None
        acc[broker_order_id] = cum
    else:
        qty = cum
    price = Decimal(str(o.get("fillPrice") or o.get("priceAvg") or o.get("price") or "0"))
    fee = Decimal(str(o.get("fee") or "0"))
    fee_asset = str(o.get("feeCcy") or "USDT")
    # Bitget v2 exposes ``execType``: "T"=taker, "M"=maker. Treat absent → taker.
    is_maker = str(o.get("execType", "T")).upper() == "M"

    return BrokerFill(
        parent_id=client_order_id,
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        trade_id=trade_id,
        qty=qty,
        price=price,
        fee=fee,
        fee_asset=fee_asset,
        ts=ts,
        is_maker=is_maker,
    )


class AsyncBitgetUserDataStream:
    """Bitget private WS — orders channel → BrokerFill async iterator.

    Lifecycle:
      1. connect → send login → wait for ``event:login code:0``
      2. send subscribe orders channel → wait for ``event:subscribe``
      3. read loop: parse data rows, push to queue
      4. on disconnect → exponential backoff reconnect

    Reuses Binance's exponential_backoff helper (no Binance-specific state).
    """

    def __init__(
        self,
        *,
        api_key: str,
        secret: str,
        passphrase: str,
        paper: bool = True,
        queue_size: int = _DEFAULT_QUEUE_SIZE,
        overflow_policy: OverflowPolicy = "block",
    ) -> None:
        self._key = api_key
        self._secret = secret
        self._passphrase = passphrase
        self._url = WS_PRIVATE_DEMO if paper else WS_PRIVATE_LIVE
        self._queue: asyncio.Queue[BrokerFill] = asyncio.Queue(maxsize=queue_size)
        self._overflow_policy = overflow_policy
        self._seen: set[tuple[str, str]] = set()
        # 2026-06-08 — per-order 누적 체결량 추적. Bitget orders 채널은 fill 마다
        # *누적* baseVolume/accBaseVolume 를 push 하므로, 각 push 를 그대로
        # qty 로 기록하면 partial fill 이 N 번이면 N 배 부풀려진다 (BSBUSDT
        # 8× 사고, logical -63856 vs broker -7982). 누적의 *증분* 만 기록한다.
        self._acc_filled: dict[str, Decimal] = {}
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ── auth payload ──────────────────────────────────────────────────────────

    def _build_login_args(self) -> dict:
        # Bitget v2 docs: timestamp is *seconds* string for WS login (not ms).
        ts = str(int(time.time()))
        prehash = f"{ts}GET/user/verify".encode()
        sig = base64.b64encode(
            hmac.new(self._secret.encode(), prehash, hashlib.sha256).digest()
        ).decode()
        return {
            "apiKey": self._key,
            "passphrase": self._passphrase,
            "timestamp": ts,
            "sign": sig,
        }

    # ── stream loop ───────────────────────────────────────────────────────────

    async def _enqueue(self, fill: BrokerFill) -> None:
        if self._overflow_policy == "block":
            await self._queue.put(fill)
            return
        try:
            self._queue.put_nowait(fill)
        except asyncio.QueueFull:
            if self._overflow_policy == "drop_oldest":
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                await self._queue.put(fill)
            else:  # raise
                raise

    async def _consume(self) -> None:
        """Single connect/login/subscribe/read cycle. Returns on disconnect or stop."""
        # 2026-06-10 keepalive — Bitget 은 앱레벨 "ping" 요구. 라이브러리 프로토콜
        # ping 은 pong 안 받아 false-disconnect → ping_interval=None + app heartbeat.
        async with websockets.connect(self._url, ping_interval=None) as ws:
            # 1. login
            await ws.send(json.dumps({"op": "login", "args": [self._build_login_args()]}))
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            ev = json.loads(raw)
            if ev.get("event") != "login" or int(ev.get("code", -1)) != 0:
                raise WSConfigError(f"bitget login failed: {ev}")
            log.info("bitget WS login OK connId=%s", ev.get("connId"))

            # 2. subscribe orders
            sub = {
                "op": "subscribe",
                "args": [{
                    "instType": "USDT-FUTURES",
                    "channel": "orders",
                    "instId": "default",
                }],
            }
            await ws.send(json.dumps(sub))
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            ev = json.loads(raw)
            if ev.get("event") not in {"subscribe", None}:
                raise WSConfigError(f"bitget subscribe failed: {ev}")
            log.info("bitget WS subscribed to orders channel")

            # 3. app-level keepalive + read loop (2026-06-10)
            from src.live.ws_keepalive import (
                app_level_heartbeat,
                is_keepalive_frame,
            )
            hb = asyncio.create_task(
                app_level_heartbeat(ws, stop_check=self._stop.is_set)
            )
            try:
                while not self._stop.is_set():
                    try:
                        raw = await asyncio.wait_for(
                            ws.recv(), timeout=_PING_INTERVAL_SEC * 2,
                        )
                    except asyncio.TimeoutError:
                        # 무메시지 — app heartbeat 가 연결 유지, 계속.
                        continue
                    if is_keepalive_frame(raw):
                        continue  # Bitget "pong" — JSON 아님, skip
                    msg = json.loads(raw)
                    # ``arg.channel == "orders"`` rows → fills.
                    arg = msg.get("arg") or {}
                    if arg.get("channel") != "orders":
                        continue
                    for row in msg.get("data") or []:
                        fill = _parse_fill_from_order(row, self._seen, self._acc_filled)
                        if fill is not None:
                            await self._enqueue(fill)
            finally:
                hb.cancel()

    async def _run(self) -> None:
        """Outer loop with exponential backoff on disconnect."""
        attempt = 0
        while not self._stop.is_set() and attempt < _RECONNECT_MAX_ATTEMPTS:
            try:
                await self._consume()
                attempt = 0  # clean disconnect — reset
            except (websockets.exceptions.ConnectionClosed,
                    asyncio.TimeoutError,
                    OSError) as exc:
                attempt += 1
                log.warning("bitget WS disconnect (attempt %d): %s — retry with backoff",
                            attempt, exc)
                await exponential_backoff(attempt)
            except WSConfigError:
                # login / subscribe failure — fatal, don't retry forever
                raise

        if attempt >= _RECONNECT_MAX_ATTEMPTS:
            raise WSDisconnectedError(
                f"bitget WS gave up after {_RECONNECT_MAX_ATTEMPTS} reconnect attempts"
            )

    # ── public API ────────────────────────────────────────────────────────────

    def stream_fills(self) -> AsyncIterator[BrokerFill]:
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        return self._iterator()

    async def _iterator(self) -> AsyncIterator[BrokerFill]:
        while not self._stop.is_set():
            try:
                fill = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield fill
            except asyncio.TimeoutError:
                if self._task and self._task.done() and self._task.exception() is not None:
                    raise self._task.exception()
                continue

    async def aclose(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
