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
from src.brokers.binance.symbol_filters import SymbolFilters
from src.brokers.errors import BrokerClosedError, BrokerStartupError, InvalidOrderError
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
        ws_base_url: str = "wss://fstream.binance.com/ws",  # /ws req. (user-data 404 else)
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
        # #238 Bug A — real exchangeInfo LOT_SIZE source (TTL-cached). The
        # conversion layer only quantizes to a coarse 0.001 fallback for
        # non-whitelisted USDT pairs; the live exchange step for many perps
        # (TRX/KITE/...) is coarser (e.g. 1), so an unfloored qty (832.840,
        # 1373.141) is rejected by Binance with -1111 every time. Mirrors the
        # sync BinanceFuturesAdapter (adapter.py:55).
        self._symbol_filters = SymbolFilters(base_url=base_url)
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

        # #238 Bug A — floor req.qty DOWN to the real exchangeInfo LOT_SIZE
        # stepSize (authoritative, covers ALL symbols — not a whitelist).
        # Without this, a non-whitelisted USDT pair (TRX/KITE) carries the
        # conversion-layer 0.001 fallback (e.g. 832.840) while its real step is
        # coarser → Binance rejects every order with -1111 ("Precision is over
        # the maximum"). A sub-minQty result must NOT be submitted: a
        # guaranteed-reject flood is exactly the #238 incident class.
        req = self._quantize_qty_to_lot(req)

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

    def _quantize_qty_to_lot(self, req: OrderRequest) -> OrderRequest:
        """Floor ``req.qty`` DOWN to the symbol's real LOT_SIZE stepSize.

        Returns a new ``OrderRequest`` with the floored qty (the caller's
        request is left untouched — mirrors how ``cid`` is threaded separately
        from ``req``).

        - quantized qty < minQty (incl. floored-to-zero) → raise
          ``InvalidOrderError`` so the executor down-grades to a REJECTED ack.
          NEVER submit a guaranteed-reject (the -1111/-4164 flood = #238).
        - ``SymbolFilters`` can't resolve the symbol (unknown / exchangeInfo
          fetch failed) → safe fallback: keep the current qty + log, never
          crash the order path.
        """
        from dataclasses import replace  # noqa: PLC0415

        try:
            step = self._symbol_filters.lot_step(req.symbol)
            min_qty = self._symbol_filters.min_qty(req.symbol)
        except Exception as exc:  # noqa: BLE001 — see below
            # Safe fallback (task contract): SymbolFilters can't resolve the
            # symbol — unknown symbol (BrokerError/ValidationError) OR the
            # exchangeInfo HTTP fetch failed (requests.ConnectionError /
            # Timeout, JSON/validation error). A filter-fetch failure must
            # NEVER break live order submission, so we deliberately catch
            # broadly here and preserve pre-#238 behaviour (submit current
            # qty); the exchange may still reject, but we never crash the
            # order path nor flood it.
            log.warning(
                "lot-size filter unavailable for %s (%s); submitting un-floored "
                "qty %s — broker may still reject",
                req.symbol, exc, req.qty,
            )
            return req

        floored = (req.qty // step) * step
        floored = floored.quantize(step)

        if floored < min_qty:
            raise InvalidOrderError(
                f"{req.symbol}: qty {req.qty} floored to {floored} "
                f"(step {step}) < minQty {min_qty}; order dropped (not submitted)"
            )

        if floored == req.qty:
            return req  # already aligned — byte-identical, no allocation churn
        log.info(
            "quantized %s qty %s → %s (LOT_SIZE step %s)",
            req.symbol, req.qty, floored, step,
        )
        return replace(req, qty=floored)

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
