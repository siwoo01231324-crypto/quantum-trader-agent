from __future__ import annotations
import asyncio
import json
import logging
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

    URL: wss://fstream.binance.com/ws/{symbol_lower}@aggTrade
    Message: {"e":"aggTrade", "E":server_ts_ms, "s":symbol, "p":price (str), "q":qty (str), ...}
    """
    BASE_URL = "wss://fstream.binance.com/ws"

    def __init__(self, symbols: list[str] | None = None) -> None:
        self._symbols: list[str] = list(symbols or [])
        self._ws = None
        self._closed = False

    async def connect(self) -> None:
        # 첫 symbol 의 aggTrade stream 으로 연결 (multi-symbol 은 combined stream 향후 확장)
        # Phase 1: 단일 symbol (BTCUSDT) 사용 가정
        import websockets
        if not self._symbols:
            raise RuntimeError("No symbols subscribed")
        url = f"{self.BASE_URL}/{self._symbols[0].lower()}@aggTrade"
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
