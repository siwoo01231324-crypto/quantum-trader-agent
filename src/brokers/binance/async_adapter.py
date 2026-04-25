"""Binance USDS-M Futures async adapter implementing AsyncBrokerAdapter protocol."""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from src.brokers import client_id as client_id_mod
from src.brokers.async_rate_limiter import AsyncBinanceRateLimiter
from src.brokers.base import (
    AsyncBrokerAdapter,
    Balance,
    HealthStatus,
    MarginType,
    OrderAck,
    OrderRequest,
    Position,
    PositionSide,
)
from src.brokers.binance.async_http import AsyncBinanceFuturesClient
from src.brokers.binance.async_ws import AsyncBinanceUserDataStream, OverflowPolicy
from src.brokers.errors import BrokerClosedError, BrokerStartupError
from src.brokers.types import BrokerFill

log = logging.getLogger(__name__)

_BINANCE_CLIENT_ID_RE = re.compile(client_id_mod.BINANCE_CLIENT_ID_PATTERN)


class AsyncBinanceFuturesAdapter:
    """AsyncBrokerAdapter implementation for Binance USDS-M Futures (REST only in C3).

    stream_fills() raises NotImplementedError until async_ws.py is integrated (C4).
    """

    name = "binance_futures_async"

    def __init__(
        self,
        api_key: str,
        secret: str,
        base_url: str,
        ws_base_url: str = "wss://fstream.binance.com",
        paper: bool = True,
        kill_switch: object | None = None,
        fill_queue_size: int = 1000,
        overflow_policy: OverflowPolicy = "block",
    ) -> None:
        self.paper = paper
        self._kill_switch = kill_switch
        self._ws_base_url = ws_base_url.rstrip("/")
        self._fill_queue_size = fill_queue_size
        self._overflow_policy = overflow_policy
        self._closing = False

        rate_limiter = AsyncBinanceRateLimiter()
        self._client = AsyncBinanceFuturesClient(
            api_key=api_key,
            secret=secret,
            base_url=base_url,
            rate_limiter=rate_limiter,
        )
        self._hedge_mode: bool | None = None
        self._ws_stream: AsyncBinanceUserDataStream | None = None
        self._inflight: list[asyncio.Task] = []

    # ── kill switch gate ──────────────────────────────────────────────────────

    def _assert_allow_order(self, emergency_exit: bool) -> None:
        if self._closing:
            raise BrokerClosedError("Adapter is closing; new orders are rejected.")
        if self._kill_switch is not None:
            self._kill_switch.assert_allow_order(liquidation=emergency_exit)

    # ── AsyncBrokerAdapter methods ────────────────────────────────────────────

    async def place_order(self, req: OrderRequest) -> OrderAck:
        self._assert_allow_order(req.emergency_exit)  # KillSwitch gate — must be first

        await self._client._rate_limiter.acquire("orders_1m")
        await self._client._rate_limiter.acquire("orders_10s")

        if _BINANCE_CLIENT_ID_RE.match(req.client_order_id):
            cid = req.client_order_id
        else:
            cid = client_id_mod.generate(
                strategy="fallback",
                symbol=req.symbol,
                side=req.side.value,
                ts_ms=self._client._now_ms(),
            )
            log.warning(
                "client_order_id %r failed Binance regex; using generated %r",
                req.client_order_id,
                cid,
            )

        resp = await self._client.place_order(req, cid)
        return OrderAck(
            broker_order_id=str(resp.orderId),
            client_order_id=resp.clientOrderId,
            symbol=resp.symbol,
            status=resp.status,
            ts=datetime.fromtimestamp(resp.updateTime / 1000, tz=timezone.utc),
            qty=resp.origQty,
            price=resp.price if resp.price != Decimal("0") else None,
        )

    async def cancel_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> None:
        await self._client.cancel_order(
            symbol,
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
        )

    async def get_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> OrderAck:
        resp = await self._client.get_order(
            symbol,
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
        )
        return OrderAck(
            broker_order_id=str(resp.orderId),
            client_order_id=resp.clientOrderId,
            symbol=resp.symbol,
            status=resp.status,
            ts=datetime.fromtimestamp(resp.updateTime / 1000, tz=timezone.utc),
            qty=resp.origQty,
            price=resp.price if resp.price != Decimal("0") else None,
        )

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        risks = await self._client.get_position_risk(symbol)
        positions = []
        for r in risks:
            if r.positionAmt == Decimal("0"):
                continue
            side = PositionSide(r.positionSide)
            positions.append(
                Position(
                    symbol=r.symbol,
                    side=side,
                    qty=abs(r.positionAmt),
                    entry_price=r.entryPrice,
                    liquidation_price=r.liquidationPrice if r.liquidationPrice != Decimal("0") else None,
                )
            )
        return positions

    async def get_balance(self) -> list[Balance]:
        items = await self._client.get_balance()
        return [
            Balance(
                asset=b.asset,
                free=b.availableBalance,
                locked=b.balance - b.availableBalance,
            )
            for b in items
        ]

    def stream_fills(self) -> AsyncIterator[BrokerFill]:
        """Return an AsyncIterator that yields BrokerFill from the user-data WS stream."""
        self._ws_stream = AsyncBinanceUserDataStream(
            client=self._client,
            ws_base_url=self._ws_base_url,
            queue_size=self._fill_queue_size,
            overflow_policy=self._overflow_policy,
        )
        return self._ws_stream.stream_fills()

    async def health_check(self) -> HealthStatus:
        try:
            await self._client.ping()
            return HealthStatus.OK
        except Exception:
            return HealthStatus.DOWN

    # ── ensure_* (idempotent) ─────────────────────────────────────────────────

    async def ensure_leverage(self, symbol: str, leverage: int) -> None:
        risks = await self._client.get_position_risk(symbol)
        if risks and risks[0].leverage == leverage:
            return
        await self._client.set_leverage(symbol, leverage)

    async def ensure_margin_type(self, symbol: str, mode: MarginType) -> None:
        risks = await self._client.get_position_risk(symbol)
        if risks:
            current = risks[0].marginType.upper()
            if current == mode.value:
                return
        await self._client.set_margin_type(symbol, mode)

    async def ensure_position_mode(self, *, hedge: bool) -> None:
        current_hedge = await self._client.get_position_mode()
        if current_hedge != hedge:
            raise BrokerStartupError(
                f"Position mode mismatch: expected hedge={hedge}, "
                f"but exchange has hedge={current_hedge}. "
                "Change position mode manually before starting the adapter."
            )
        self._hedge_mode = hedge

    # ── aclose (5-stage contract) ─────────────────────────────────────────────

    async def aclose(self) -> None:
        """Close adapter in strict order (idempotent).

        Stage 1: reject new orders (closing=True)
        Stage 2: WS close frame + wait_closed (via AsyncBinanceUserDataStream)
        Stage 3: listenKey keepalive task cancel + await
        Stage 4: inflight REST CancelledError propagation
        Stage 5: httpx.AsyncClient.aclose()
        """
        if self._closing:
            return
        # Stage 1: reject new orders
        self._closing = True

        # Stage 2+3: WS close + listenKey keepalive cancel (via stream aclose)
        if self._ws_stream is not None:
            await self._ws_stream.aclose()

        # Stage 4: cancel inflight REST tasks and await their CancelledError
        if self._inflight:
            for task in self._inflight:
                task.cancel()
            await asyncio.gather(*self._inflight, return_exceptions=True)
            self._inflight.clear()

        # Stage 5: httpx client
        await self._client.aclose()
