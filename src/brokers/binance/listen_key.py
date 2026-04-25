"""Binance listenKey lifecycle helper (async).

Responsibilities:
- Issue a new listenKey via POST /fapi/v1/listenKey
- Extend it every 30 min via PUT /fapi/v1/listenKey
- Delete it on clean shutdown via DELETE /fapi/v1/listenKey
- Run a keepalive asyncio.Task that extends the key and propagates
  ListenKeyExpiredError to the caller if keepalive fails unrecoverably.
"""
from __future__ import annotations

import asyncio
import logging

from src.brokers.binance.async_http import AsyncBinanceFuturesClient
from src.brokers.errors import ListenKeyExpiredError

log = logging.getLogger(__name__)

_KEEPALIVE_INTERVAL_S = 30 * 60   # extend every 30 minutes
_MAX_KEEPALIVE_FAILURES = 3        # give up after 3 consecutive failures


class ListenKeyManager:
    """Manages the lifecycle of a Binance user-data stream listenKey.

    Usage::

        mgr = ListenKeyManager(client)
        key = await mgr.issue()
        mgr.start_keepalive(expiry_event)   # fires expiry_event on fatal failure
        ...
        await mgr.stop_keepalive()
        await mgr.delete()
    """

    def __init__(self, client: AsyncBinanceFuturesClient) -> None:
        self._client = client
        self._key: str | None = None
        self._keepalive_task: asyncio.Task | None = None

    @property
    def key(self) -> str:
        if self._key is None:
            raise RuntimeError("listenKey not yet issued")
        return self._key

    async def issue(self) -> str:
        self._key = await self._client.issue_listen_key()
        log.info("listenKey issued: %s...", self._key[:8])
        return self._key

    async def delete(self) -> None:
        if self._key is not None:
            try:
                await self._client.delete_listen_key(self._key)
                log.info("listenKey deleted")
            except Exception as exc:
                log.warning("listenKey delete failed (ignored): %s", exc)
            self._key = None

    def start_keepalive(self, expiry_event: asyncio.Event) -> None:
        """Start background keepalive task. Sets expiry_event on fatal failure."""
        self._keepalive_task = asyncio.get_event_loop().create_task(
            self._keepalive_loop(expiry_event),
            name="binance-listenkey-keepalive",
        )

    async def stop_keepalive(self) -> None:
        """Cancel and await the keepalive task."""
        if self._keepalive_task is not None and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except (asyncio.CancelledError, Exception):
                pass
            self._keepalive_task = None

    async def _keepalive_loop(self, expiry_event: asyncio.Event) -> None:
        failures = 0
        while True:
            await asyncio.sleep(_KEEPALIVE_INTERVAL_S)
            try:
                if self._key is None:
                    return
                await self._client.extend_listen_key(self._key)
                log.debug("listenKey extended")
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                log.warning("listenKey keepalive failure #%d: %s", failures, exc)
                if failures >= _MAX_KEEPALIVE_FAILURES:
                    log.error("listenKey keepalive unrecoverable — signalling expiry")
                    expiry_event.set()
                    return
