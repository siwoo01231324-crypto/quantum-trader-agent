"""Binance user-data WebSocket stream — async implementation.

Provides stream_fills() as an AsyncIterator[BrokerFill].

Overflow policies for the internal asyncio.Queue:
  block       — await until space is available (default)
  drop_oldest — discard the oldest fill and enqueue the new one
  raise       — raise immediately (fills lost)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import websockets
import websockets.exceptions

from src.brokers.binance.async_http import AsyncBinanceFuturesClient
from src.brokers.binance.listen_key import ListenKeyManager
from src.brokers.async_backoff import exponential_backoff
from src.brokers.errors import (
    ListenKeyExpiredError,
    WSConfigError,
    WSDisconnectedError,
)
from src.brokers.types import BrokerFill
from src.observability.metrics import get_registry

log = logging.getLogger(__name__)

OverflowPolicy = Literal["block", "drop_oldest", "raise"]

_DEFAULT_QUEUE_SIZE = int(os.environ.get("BROKER_FILL_QUEUE_SIZE", "1000"))
_RECONNECT_MAX_ATTEMPTS = 20
_METRICS_BROKER_LABEL = "binance_futures_async"


def _parse_fill(o: dict, seen: set[tuple[str, str]]) -> BrokerFill | None:
    """Parse ORDER_TRADE_UPDATE payload. Returns None for duplicates or non-TRADE events."""
    if o.get("x") != "TRADE":
        return None

    broker_order_id = str(o.get("i", ""))
    trade_id = str(o.get("t", ""))
    dedup_key = (broker_order_id, trade_id)

    if dedup_key in seen:
        log.debug("Duplicate fill skipped: %s", dedup_key)
        return None
    seen.add(dedup_key)

    client_order_id = o.get("c", "")
    ts_ms = int(o.get("T", 0))
    ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

    return BrokerFill(
        parent_id=client_order_id,
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        trade_id=trade_id,
        qty=Decimal(str(o.get("l", "0"))),
        price=Decimal(str(o.get("L", "0"))),
        fee=Decimal(str(o.get("n", "0"))),
        fee_asset=str(o.get("N", "USDT")),
        ts=ts,
        is_maker=bool(o.get("m", False)),
    )


class AsyncBinanceUserDataStream:
    """Manages a Binance USDS-M user-data WebSocket stream as an AsyncIterator.

    Handles:
    - listenKey issue / keepalive / expiry detection
    - exponential backoff reconnect on 1006 / disconnect
    - fill dedup via (broker_order_id, trade_id) set
    - internal asyncio.Queue with configurable overflow policy
    - Prometheus metrics for reconnects, keepalive failures, queue overflow
    """

    def __init__(
        self,
        client: AsyncBinanceFuturesClient,
        ws_base_url: str,
        queue_size: int = _DEFAULT_QUEUE_SIZE,
        overflow_policy: OverflowPolicy = "block",
    ) -> None:
        self._client = client
        self._ws_base_url = ws_base_url.rstrip("/")
        self._queue_size = queue_size
        self._overflow_policy = overflow_policy
        self._listen_key_mgr = ListenKeyManager(client)
        self._queue: asyncio.Queue[BrokerFill] = asyncio.Queue(maxsize=queue_size)
        self._seen: set[tuple[str, str]] = set()
        self._closed = False
        self._expiry_event = asyncio.Event()
        # Set when the WS handshake is permanently rejected (HTTP 4xx). Makes
        # the failure non-retryable: stream_fills() raises this instead of a
        # generic ListenKeyExpiredError so the consumer fails fast (no storm).
        self._fatal_error: Exception | None = None
        self._metrics = get_registry()

    async def stream_fills(self) -> AsyncIterator[BrokerFill]:
        """Yield BrokerFill objects from the user-data stream.

        Automatically reconnects on disconnect. Raises ListenKeyExpiredError
        when the keepalive task fails unrecoverably.
        """
        listen_key = await self._listen_key_mgr.issue()
        self._listen_key_mgr.start_keepalive(self._expiry_event)

        reader_task = asyncio.get_event_loop().create_task(
            self._ws_reader_loop(listen_key),
            name="binance-ws-reader",
        )

        try:
            while not self._closed:
                # Check for listenKey expiry signal
                if self._expiry_event.is_set():
                    # A permanent handshake-config failure surfaces as the
                    # non-retryable WSConfigError so the consumer fails fast
                    # instead of treating it as a transient/expiry retry.
                    if self._fatal_error is not None:
                        raise self._fatal_error
                    raise ListenKeyExpiredError(
                        "Binance listenKey expired; fill stream cannot continue"
                    )

                try:
                    fill = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                    yield fill
                except asyncio.TimeoutError:
                    continue
        finally:
            reader_task.cancel()
            try:
                await reader_task
            except (asyncio.CancelledError, Exception):
                pass
            await self._listen_key_mgr.stop_keepalive()

    async def _ws_reader_loop(self, listen_key: str) -> None:
        """Connect to WS, read messages, enqueue fills. Reconnects on disconnect."""
        attempt = 0
        current_key = listen_key

        while not self._closed:
            if self._expiry_event.is_set():
                return

            url = f"{self._ws_base_url}/{current_key}"
            try:
                async with websockets.connect(url) as ws:
                    log.info("WS connected: %s...", current_key[:8])
                    attempt = 0  # reset on successful connect
                    async for raw in ws:
                        if self._closed:
                            return
                        await self._handle_message(raw)
            except asyncio.CancelledError:
                raise
            except websockets.exceptions.ConnectionClosedError as exc:
                code = exc.code if hasattr(exc, "code") else None
                log.warning("WS closed (code=%s) — reconnecting (attempt %d)", code, attempt)
                self._metrics.broker_ws_reconnect_total.labels(
                    broker=_METRICS_BROKER_LABEL, reason=f"close_{code}"
                ).inc()
            except Exception as exc:
                # A 4xx handshake rejection (esp. 404) is PERMANENT — wrong
                # ws_base_url / invalid listenKey host. Retrying can never
                # succeed; without this it storms 20×(outer 100×). Fail fast.
                status = (
                    getattr(getattr(exc, "response", None), "status_code", None)
                    or getattr(exc, "status_code", None)
                )
                if isinstance(status, int) and 400 <= status < 500:
                    log.error(
                        "WS handshake permanently rejected HTTP %s at %r — "
                        "NOT retrying. Check BINANCE_WS_BASE_URL: Binance "
                        "futures user-data WS must include the /ws path "
                        "(testnet: wss://stream.binancefuture.com/ws).",
                        status, self._ws_base_url,
                    )
                    self._metrics.broker_ws_reconnect_total.labels(
                        broker=_METRICS_BROKER_LABEL, reason=f"fatal_{status}"
                    ).inc()
                    self._fatal_error = WSConfigError(
                        f"WS handshake HTTP {status} at {self._ws_base_url} "
                        f"(/ws path or host misconfigured)"
                    )
                    self._expiry_event.set()
                    return
                log.warning("WS error: %s — reconnecting (attempt %d)", exc, attempt)
                self._metrics.broker_ws_reconnect_total.labels(
                    broker=_METRICS_BROKER_LABEL, reason="error"
                ).inc()

            if self._closed or self._expiry_event.is_set():
                return

            # Try to refresh listen key before reconnecting
            try:
                current_key = await self._listen_key_mgr.issue()
            except Exception as exc:
                log.warning("listenKey reissue failed: %s", exc)

            await exponential_backoff(attempt, base=1.0, cap=10.0, jitter=0.2)
            attempt += 1

            if attempt > _RECONNECT_MAX_ATTEMPTS:
                log.error("WS reconnect exceeded max attempts — giving up")
                self._expiry_event.set()
                return

    async def _handle_message(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            log.warning("Unparseable WS message: %r", str(raw)[:200])
            return

        if msg.get("e") != "ORDER_TRADE_UPDATE":
            return

        o = msg.get("o", {})
        fill = _parse_fill(o, self._seen)
        if fill is None:
            return

        await self._enqueue(fill)

    async def _enqueue(self, fill: BrokerFill) -> None:
        if self._overflow_policy == "block":
            await self._queue.put(fill)
        elif self._overflow_policy == "drop_oldest":
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                    self._metrics.broker_fill_queue_overflow_total.labels(
                        broker=_METRICS_BROKER_LABEL, policy="drop_oldest"
                    ).inc()
                    log.warning("Fill queue full — dropped oldest fill")
                except asyncio.QueueEmpty:
                    pass
            await self._queue.put(fill)
        elif self._overflow_policy == "raise":
            if self._queue.full():
                self._metrics.broker_fill_queue_overflow_total.labels(
                    broker=_METRICS_BROKER_LABEL, policy="raise"
                ).inc()
                raise WSDisconnectedError("Fill queue full (overflow_policy=raise)")
            self._queue.put_nowait(fill)

    async def aclose(self) -> None:
        self._closed = True
        await self._listen_key_mgr.stop_keepalive()
        await self._listen_key_mgr.delete()
