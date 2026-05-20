from __future__ import annotations
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class MarketDataFeed(Protocol):
    async def connect(self) -> None: ...
    async def subscribe(self, symbols: list[str]) -> None: ...
    def __aiter__(self) -> AsyncIterator: ...
    async def aclose(self) -> None: ...


class BinancePublicFeed:
    """Binance USDT-M Futures public aggTrade WS feed (no API key).

    Endpoint 선택 (default 우선순위):
      1. `base_url` 명시 인자
      2. `BINANCE_WS_BASE_URL` env var (e.g. `wss://stream.binancefuture.com`)
      3. mainnet futures (`wss://fstream.binance.com/ws`)

    #238 hotfix: 한국 IP 에서 mainnet (`fstream.binance.com`) 은 connect 는 되지만
    aggTrade 데이터를 0건 push (지역 차단). testnet (`stream.binancefuture.com`)
    은 정상 push. broker_mode=binance-testnet-shadow 시 caller (`_select_feed`)
    가 testnet URL 을 명시 주입해야 한다.

    Message: {"e":"aggTrade", "E":server_ts_ms, "s":symbol, "p":price, "q":qty, ...}
    """
    DEFAULT_MAINNET = "wss://fstream.binance.com/ws"
    DEFAULT_TESTNET = "wss://stream.binancefuture.com/ws"
    BASE_URL = DEFAULT_MAINNET  # backward-compat class attr

    def __init__(
        self,
        symbols: list[str] | None = None,
        *,
        base_url: str | None = None,
    ) -> None:
        self._symbols: list[str] = list(symbols or [])
        self._ws = None
        self._closed = False
        if base_url is None:
            env_url = os.environ.get("BINANCE_WS_BASE_URL")
            if env_url:
                # _build_binance_adapter 는 bare ("wss://stream.binancefuture.com") 로
                # 두므로 `/ws` 가 없으면 자동 append. 사용자가 `/ws` 포함해 줘도 OK.
                base_url = env_url if env_url.rstrip("/").endswith("/ws") else env_url.rstrip("/") + "/ws"
            else:
                base_url = self.DEFAULT_MAINNET
        self._base_url = base_url

    async def connect(self) -> None:
        # 첫 symbol 의 aggTrade stream 으로 연결 (multi-symbol 은 combined stream 향후 확장)
        # Phase 1: 단일 symbol (BTCUSDT) 사용 가정
        import websockets
        if not self._symbols:
            raise RuntimeError("No symbols subscribed")
        url = f"{self._base_url}/{self._symbols[0].lower()}@aggTrade"
        logger.info("BinancePublicFeed connecting to %s", url)
        self._ws = await websockets.connect(url)

    async def subscribe(self, symbols: list[str]) -> None:
        # Phase 1: connect 시점에 symbol 결정. subscribe 는 _symbols 업데이트만.
        self._symbols.extend(symbols)

    def __aiter__(self) -> AsyncIterator:
        return self._iter()

    async def _iter(self) -> AsyncIterator:
        from src.live.types import Tick
        if self._ws is None:
            raise RuntimeError("Feed not connected")
        async for raw in self._ws:
            if self._closed:
                break
            try:
                msg = json.loads(raw)
                if msg.get("e") != "aggTrade":
                    continue
                server_ts_ms = int(msg["E"])
                server_ts = datetime.fromtimestamp(server_ts_ms / 1000, tz=timezone.utc).isoformat()
                tick = Tick(
                    symbol=str(msg["s"]),
                    price=Decimal(str(msg["p"])),
                    qty=Decimal(str(msg["q"])),
                    ts=datetime.now(timezone.utc).isoformat(),
                    server_ts=server_ts,
                )
                yield tick
            except (json.JSONDecodeError, KeyError, ValueError) as err:
                logger.warning("BinancePublicFeed parse error: %s", err)
                continue

    async def aclose(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None


class BinanceMarkPriceFeed:
    """Binance USDT-M Futures `!markPrice@arr@1s` stream — all-symbol mark price.

    A single WS connection pushes mark prices for **every** USDT-perp symbol
    once per second. Used by ``LivePositionRiskManager`` to evaluate stop/TP
    against the *full* universe of open positions — the legacy
    ``BinancePublicFeed`` only subscribed to ONE symbol's aggTrade, so a
    universe-scanner that opened 30 positions could never trigger a stop on
    the other 29.

    Endpoint precedence mirrors :class:`BinancePublicFeed`:
      1. ``base_url`` argument
      2. ``BINANCE_WS_BASE_URL`` env var
      3. mainnet (``wss://fstream.binance.com/ws``)

    Caller (``_select_feed`` for the broker-testnet path) must inject
    ``DEFAULT_TESTNET`` when running against ``binance-testnet-shadow`` —
    same Korean-IP regional restriction as the aggTrade feed.

    Yields a list of ``(symbol, mark_price, server_ts)`` tuples per push so
    consumers can iterate without re-parsing JSON. Mark price (Binance's
    funding/liquidation reference) is preferred over aggTrade last for
    stop/TP evaluation: it's smoothed against tick noise *and* matches the
    price Binance itself uses for forced liquidation.
    """
    DEFAULT_MAINNET = "wss://fstream.binance.com/ws"
    DEFAULT_TESTNET = "wss://stream.binancefuture.com/ws"
    STREAM_PATH = "!markPrice@arr@1s"

    def __init__(self, *, base_url: str | None = None) -> None:
        self._ws = None
        self._closed = False
        if base_url is None:
            env_url = os.environ.get("BINANCE_WS_BASE_URL")
            if env_url:
                base_url = (
                    env_url if env_url.rstrip("/").endswith("/ws")
                    else env_url.rstrip("/") + "/ws"
                )
            else:
                base_url = self.DEFAULT_MAINNET
        self._base_url = base_url

    async def connect(self) -> None:
        import websockets
        url = f"{self._base_url}/{self.STREAM_PATH}"
        logger.info("BinanceMarkPriceFeed connecting to %s", url)
        self._ws = await websockets.connect(url)

    def __aiter__(self) -> AsyncIterator:
        return self._iter()

    async def _iter(self) -> AsyncIterator:
        """Yield ``list[tuple[str, Decimal, datetime]]`` per WS push.

        Payload is a JSON array of ``markPriceUpdate`` events; we drop other
        message shapes (defensive — Binance occasionally interleaves
        listener-key error frames).
        """
        if self._ws is None:
            raise RuntimeError("Feed not connected")
        async for raw in self._ws:
            if self._closed:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as err:
                logger.warning("BinanceMarkPriceFeed parse error: %s", err)
                continue
            # `!markPrice@arr@1s` always pushes an array. Single-symbol shape
            # never occurs here, but guard defensively.
            if not isinstance(msg, list):
                continue
            batch: list[tuple[str, Decimal, datetime]] = []
            for ev in msg:
                if not isinstance(ev, dict) or ev.get("e") != "markPriceUpdate":
                    continue
                try:
                    sym = str(ev["s"])
                    price = Decimal(str(ev["p"]))
                    ts_ms = int(ev.get("E") or ev.get("T") or 0)
                    ts = (
                        datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                        if ts_ms
                        else datetime.now(timezone.utc)
                    )
                    batch.append((sym, price, ts))
                except (KeyError, ValueError) as err:
                    logger.warning(
                        "BinanceMarkPriceFeed event skip (%s): %r", err, ev,
                    )
                    continue
            if batch:
                yield batch

    async def aclose(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
