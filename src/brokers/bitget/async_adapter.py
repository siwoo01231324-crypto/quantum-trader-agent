"""Bitget USDT-M Futures async adapter implementing AsyncBrokerAdapter protocol.

REST-only (P1). WS user-data + market-data come in P2 (``async_ws.py`` /
``market_ws.py``). ``stream_fills()`` raises ``NotImplementedError`` for
parity with Binance's pre-WS Phase 1.

P1 limitations (documented for review):
  - One-way mode only (PositionSide.BOTH). Hedge mode (LONG/SHORT separate
    accounting) requires tradeSide=open/close — implemented but not exercised
    in this commit's CLI integration.
  - Bitget v2 expects ``size`` denominated in *base coin* units (e.g. BTC for
    BTCUSDT). Our OrderRequest.qty already uses base-coin units (consistent
    with Binance Futures). 1000PEPE-style multiplier (Binance ``1000PEPEUSDT``
    has no Bitget equivalent) is translated at the strategy-config layer, not
    here.
  - max-notional cooldown (Binance -2027 pattern) ported as-is using Bitget
    "40774" code.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.brokers import client_id as client_id_mod
from src.brokers.base import (
    AsyncBrokerAdapter,
    Balance,
    HealthStatus,
    MarginType,
    OrderAck,
    OrderRequest,
    OrderType,
    Position,
    PositionSide,
)
from src.brokers.bitget.async_http import (
    AsyncBitgetFuturesClient,
    DEMO_PRODUCT_TYPE,
    LIVE_PRODUCT_TYPE,
    REST_BASE_LIVE,
)
from src.brokers.bitget.async_ws import AsyncBitgetUserDataStream, OverflowPolicy
from src.brokers.bitget.symbol_filters import SymbolFilters
from src.brokers.errors import (
    BrokerClosedError,
    BrokerStartupError,
    InvalidOrderError,
)
from src.brokers.types import BrokerFill
from src.execution.base import Side
from src.observability.metrics import Metrics  # type: ignore  # noqa: F401  (parity)

log = logging.getLogger(__name__)


class AsyncBitgetFuturesAdapter:
    """AsyncBrokerAdapter implementation for Bitget USDT-M Futures.

    Demo trading is selected by ``paper=True`` (paptrading header + SUSDT-FUTURES).
    """

    name = "bitget_futures_async"

    def __init__(
        self,
        *,
        api_key: str,
        secret: str,
        passphrase: str,
        base_url: str = REST_BASE_LIVE,
        paper: bool = True,
        kill_switch: object | None = None,
        fill_queue_size: int = 1000,
        overflow_policy: "OverflowPolicy" = "block",
    ) -> None:
        self.paper = paper
        self._kill_switch = kill_switch
        self._closing = False
        self._client = AsyncBitgetFuturesClient(
            api_key=api_key, secret=secret, passphrase=passphrase,
            base_url=base_url, paper=paper,
        )
        self._product_type = DEMO_PRODUCT_TYPE if paper else LIVE_PRODUCT_TYPE
        self._symbol_filters = SymbolFilters(base_url=base_url, paper=paper)
        self._hedge_mode: bool | None = None
        # Bitget -2027 equivalent: code 40762 (qty exceeds upper limit).
        # Local cooldown to prevent rate-limit cascade (mirrors Binance fix).
        self._max_notional_cooldown: dict[tuple[str, str], float] = {}
        self._MAX_NOTIONAL_COOLDOWN_SEC: float = 300.0
        # WS user-data stream — lazily built on first stream_fills().
        self._ws_stream: AsyncBitgetUserDataStream | None = None
        self._ws_creds = (api_key, secret, passphrase)
        self._ws_paper = paper
        self._fill_queue_size = fill_queue_size
        self._overflow_policy: "OverflowPolicy" = overflow_policy

    # ── kill switch ──────────────────────────────────────────────────────────

    def _assert_allow_order(self, emergency_exit: bool) -> None:
        if self._closing:
            raise BrokerClosedError("Adapter is closing; new orders are rejected.")
        if self._kill_switch is not None:
            self._kill_switch.assert_allow_order(liquidation=emergency_exit)

    # ── place_order ──────────────────────────────────────────────────────────

    async def place_order(self, req: OrderRequest) -> OrderAck:
        self._assert_allow_order(req.emergency_exit)

        # Pre-flight cooldown — same (symbol, side) blocked 5min after a 40774.
        cooldown_key = (req.symbol, req.side.value)
        expiry = self._max_notional_cooldown.get(cooldown_key)
        if expiry is not None:
            now = time.monotonic()
            if now < expiry:
                log.info(
                    "bitget place_order skipped — max-notional cooldown %s %s remaining=%.0fs",
                    req.symbol, req.side.value, expiry - now,
                )
                return OrderAck(
                    broker_order_id="",
                    client_order_id=req.client_order_id,
                    symbol=req.symbol,
                    status="REJECTED",
                    ts=datetime.now(tz=timezone.utc),
                    qty=req.qty,
                    price=None,
                )
            else:
                self._max_notional_cooldown.pop(cooldown_key, None)

        # Quantize qty to Bitget's sizeMultiplier (LOT_SIZE-equivalent).
        req = self._quantize_qty(req)
        if req.order_type == OrderType.LIMIT and req.price is not None:
            req = self._quantize_price(req)

        # Side mapping: our enum value → Bitget literal.
        side_str = "buy" if req.side == Side.BUY else "sell"
        order_type_str = "market" if req.order_type == OrderType.MARKET else "limit"

        # Hedge mode tradeSide hint.
        trade_side: str | None = None
        if req.position_side != PositionSide.BOTH:
            # LONG enter = open, LONG exit = close (handled by caller intent).
            trade_side = "close" if req.reduce_only else "open"

        cid = self._normalize_cid(req.client_order_id, req)

        try:
            resp = await self._client.place_order(
                symbol=req.symbol,
                side=side_str,
                order_type=order_type_str,
                size=req.qty,
                price=req.price if req.order_type == OrderType.LIMIT else None,
                client_oid=cid,
                trade_side=trade_side,
                reduce_only=req.reduce_only,
            )
        except InvalidOrderError as exc:
            # 40762 = order qty exceeds upper limit (= Binance -2027 equivalent).
            # 40774 looks like a max-notional error by name but Bitget actually
            # uses it for "order type / position mode mismatch" — NOT cooldown.
            err_str = str(exc)
            if "[40762]" in err_str or "[40774]" in err_str and "exceeds" in err_str.lower():
                self._max_notional_cooldown[cooldown_key] = (
                    time.monotonic() + self._MAX_NOTIONAL_COOLDOWN_SEC
                )
                log.warning(
                    "bitget max_notional_cooldown registered %s %s for %.0fs",
                    req.symbol, req.side.value, self._MAX_NOTIONAL_COOLDOWN_SEC,
                )
            raise

        # Bitget place-order response is minimal — fetch detail for status.
        return OrderAck(
            broker_order_id=str(resp.orderId),
            client_order_id=str(resp.clientOid or cid),
            symbol=req.symbol,
            status="NEW",  # Bitget place response doesn't echo status; treat as NEW.
            ts=datetime.now(tz=timezone.utc),
            qty=req.qty,
            price=req.price if req.order_type == OrderType.LIMIT else None,
        )

    def _normalize_cid(self, raw_cid: str, req: OrderRequest) -> str:
        # Bitget v2 클라이언트 OID 는 1~36자, 영숫자+_-. 패턴 외엔 fallback.
        if raw_cid and len(raw_cid) <= 36 and all(c.isalnum() or c in "_-" for c in raw_cid):
            return raw_cid
        return client_id_mod.generate(
            strategy="fallback",
            symbol=req.symbol,
            side=req.side.value,
            ts_ms=int(time.time() * 1000),
        )

    def _quantize_qty(self, req: OrderRequest) -> OrderRequest:
        from dataclasses import replace  # noqa: PLC0415
        try:
            step = self._symbol_filters.lot_step(req.symbol)
            min_qty = self._symbol_filters.min_qty(req.symbol)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "bitget lot filter unavailable for %s (%s); submitting un-floored qty %s",
                req.symbol, exc, req.qty,
            )
            return req
        floored = (req.qty // step) * step
        floored = floored.quantize(step)
        if floored < min_qty:
            raise InvalidOrderError(
                f"{req.symbol}: qty {req.qty} floored to {floored} (step {step}) "
                f"< minQty {min_qty}; order dropped"
            )
        if floored == req.qty:
            return req
        log.info("bitget quantized %s qty %s → %s (step %s)",
                 req.symbol, req.qty, floored, step)
        return replace(req, qty=floored)

    def _quantize_price(self, req: OrderRequest) -> OrderRequest:
        from dataclasses import replace  # noqa: PLC0415
        if req.price is None:
            return req
        try:
            quantized = self._symbol_filters.quantize_price(req.symbol, req.price)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "bitget price filter unavailable for %s (%s); submitting un-quantized %s",
                req.symbol, exc, req.price,
            )
            return req
        if quantized == req.price:
            return req
        log.info("bitget quantized %s price %s → %s", req.symbol, req.price, quantized)
        return replace(req, price=quantized)

    # ── cancel / get_order ───────────────────────────────────────────────────

    async def cancel_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> None:
        await self._client.cancel_order(
            symbol=symbol,
            order_id=broker_order_id,
            client_oid=client_order_id,
        )

    async def get_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> OrderAck:
        resp = await self._client.get_order_detail(
            symbol=symbol,
            order_id=broker_order_id,
            client_oid=client_order_id,
        )
        # Bitget status: live / partially_filled / filled / canceled.
        status_map = {
            "live": "NEW",
            "new": "NEW",
            "partially_filled": "PARTIALLY_FILLED",
            "filled": "FILLED",
            "canceled": "CANCELED",
            "cancelled": "CANCELED",
        }
        ts = datetime.fromtimestamp(resp.utime / 1000, tz=timezone.utc) if resp.utime else datetime.now(tz=timezone.utc)
        return OrderAck(
            broker_order_id=resp.orderId,
            client_order_id=resp.clientOid,
            symbol=resp.symbol,
            status=status_map.get(resp.status.lower(), resp.status.upper()),
            ts=ts,
            qty=resp.size,
            price=resp.priceAvg if resp.priceAvg else resp.price,
            filled_qty=resp.filledSize,
        )

    # ── positions / balance ──────────────────────────────────────────────────

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        if symbol is not None:
            raw = await self._client.get_single_position(symbol=symbol)
        else:
            raw = await self._client.get_all_positions()
        positions: list[Position] = []
        for p in raw:
            if p.total == Decimal("0"):
                continue
            # Bitget holdSide → PositionSide.
            side = PositionSide.LONG if p.holdSide == "long" else PositionSide.SHORT
            positions.append(
                Position(
                    symbol=p.symbol,
                    side=side,
                    qty=p.total,
                    entry_price=p.averageOpenPrice,
                    liquidation_price=p.liquidationPrice,
                )
            )
        return positions

    async def get_balance(self) -> list[Balance]:
        # USDT margin coin only (matches whitelist + production.yaml).
        # MVP: query BTCUSDT as proxy symbol for marginCoin=USDT account.
        try:
            acc = await self._client.get_account(symbol="BTCUSDT", margin_coin="USDT")
        except Exception as exc:  # noqa: BLE001
            log.warning("bitget get_balance failed: %s", exc)
            return []
        return [Balance(asset=acc.marginCoin, free=acc.available, locked=acc.locked)]

    # ── ensure_* ─────────────────────────────────────────────────────────────

    async def ensure_leverage(self, symbol: str, leverage: int) -> None:
        try:
            await self._client.set_leverage(symbol=symbol, leverage=leverage)
        except InvalidOrderError as exc:
            log.warning("bitget set_leverage failed sym=%s lev=%d: %s", symbol, leverage, exc)
            raise

    async def ensure_leverage_minimum(
        self, symbol: str, fallback_leverage: int = 1,
    ) -> None:
        """Mirror Binance helper — Bitget 도 종목 leverage 미설정 시 처음 발주가
        실패할 수 있어 first-trade 직전 안전망. 기본은 1x.
        """
        try:
            poss = await self._client.get_single_position(symbol=symbol)
            if poss and poss[0].leverage > 0:
                return
        except Exception:  # noqa: BLE001
            pass
        await self.ensure_leverage(symbol, fallback_leverage)

    async def ensure_margin_type(self, symbol: str, mode: MarginType) -> None:
        bitget_mode = "crossed" if mode == MarginType.CROSSED else "isolated"
        await self._client.set_margin_mode(symbol=symbol, mode=bitget_mode)

    async def ensure_position_mode(self, *, hedge: bool) -> None:
        if self._hedge_mode is not None and self._hedge_mode == hedge:
            return
        try:
            await self._client.set_position_mode(hedge=hedge)
            self._hedge_mode = hedge
        except Exception as exc:  # noqa: BLE001
            log.warning("bitget ensure_position_mode failed: %s", exc)
            raise BrokerStartupError(
                f"Bitget position mode set failed (target hedge={hedge}): {exc}"
            ) from exc

    # ── health / lifecycle ───────────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        try:
            await self._client.ping()
            return HealthStatus.OK
        except Exception:  # noqa: BLE001
            return HealthStatus.DOWN

    def stream_fills(self) -> AsyncIterator[BrokerFill]:
        """Return an AsyncIterator that yields BrokerFill from Bitget private WS."""
        key, sec, pas = self._ws_creds
        self._ws_stream = AsyncBitgetUserDataStream(
            api_key=key, secret=sec, passphrase=pas,
            paper=self._ws_paper,
            queue_size=self._fill_queue_size,
            overflow_policy=self._overflow_policy,
        )
        return self._ws_stream.stream_fills()

    async def aclose(self) -> None:
        self._closing = True
        if self._ws_stream is not None:
            await self._ws_stream.aclose()
        await self._client.aclose()
