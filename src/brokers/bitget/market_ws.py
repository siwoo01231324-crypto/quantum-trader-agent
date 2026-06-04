"""Bitget v2 public WebSocket — market data (kline + ticker/mark price).

Parallels ``src/brokers/binance/market_ws.py`` API surface. No auth (public WS).

WS endpoints:
  - Demo: ``wss://wspap.bitget.com/v2/ws/public``
  - Live: ``wss://ws.bitget.com/v2/ws/public``

Channels:
  - ``candle{interval}``  — e.g. ``candle1H``, ``candle5m``. Per-symbol kline.
  - ``ticker``            — per-symbol stats including ``lastPr`` AND ``markPrice``.
    Bitget v2 does NOT have a per-second all-symbols mark-price stream like
    Binance's ``!markPrice@arr@1s`` — we subscribe per symbol (one entry per
    universe symbol). Push frequency: ~once per trade tick (sub-second).

Bootstrap (REST):
  - ``/api/v2/mix/market/candles`` for historical klines used by strategies'
    ``MIN_HISTORY`` warmup. Mirrors ``bootstrap_history`` in Binance module.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import httpx
import websockets
import websockets.exceptions

from src.brokers.async_backoff import exponential_backoff
from src.brokers.bitget.async_http import REST_BASE_LIVE

log = logging.getLogger(__name__)

WS_PUBLIC_LIVE = "wss://ws.bitget.com/v2/ws/public"
WS_PUBLIC_DEMO = "wss://wspap.bitget.com/v2/ws/public"

_PING_INTERVAL_SEC = 20.0
_RECONNECT_MAX_ATTEMPTS = 20

# Bitget v2 candle channel intervals.
_CANDLE_INTERVAL_CHANNEL: dict[str, str] = {
    "1m": "candle1m", "5m": "candle5m", "15m": "candle15m",
    "30m": "candle30m", "1h": "candle1H", "4h": "candle4H",
    "1d": "candle1D",
}


@dataclass(frozen=True)
class KlineEvent:
    """Mirrors Binance KlineEvent shape so snapshot_builder is broker-agnostic."""
    symbol: str
    interval: str
    open_time: int      # ms
    close_time: int     # ms (open_time + interval - 1)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    closed: bool        # True 면 봉 마감 (`k.x` 대응)


@dataclass(frozen=True)
class MarkPriceEvent:
    symbol: str
    mark_price: Decimal
    ts: int             # ms (push timestamp)


def _interval_to_channel(interval: str) -> str:
    ch = _CANDLE_INTERVAL_CHANNEL.get(interval)
    if ch is None:
        raise ValueError(
            f"unsupported interval '{interval}'; expected one of {sorted(_CANDLE_INTERVAL_CHANNEL)}"
        )
    return ch


def _parse_kline_row(symbol: str, interval: str, row: list, *, closed: bool) -> KlineEvent:
    """Bitget candle row: [ts, open, high, low, close, baseVol, quoteVol].

    ``ts`` is the **open time** ms. ``closed`` is True for non-latest rows
    pushed in a single update (the last row in a multi-row push is the
    currently-forming bar).
    """
    open_ms = int(row[0])
    # interval length in ms — derived from channel name suffix.
    interval_ms = _INTERVAL_MS.get(interval, 0)
    close_ms = open_ms + interval_ms - 1 if interval_ms else open_ms
    return KlineEvent(
        symbol=symbol, interval=interval,
        open_time=open_ms, close_time=close_ms,
        open=Decimal(str(row[1])), high=Decimal(str(row[2])),
        low=Decimal(str(row[3])), close=Decimal(str(row[4])),
        volume=Decimal(str(row[5])),
        closed=closed,
    )


_INTERVAL_MS: dict[str, int] = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000,
    "1d": 86_400_000,
}


def _parse_ticker_row(row: dict) -> MarkPriceEvent | None:
    """Bitget ticker row → MarkPriceEvent. Ignore rows w/o markPrice."""
    mark = row.get("markPrice")
    if mark is None or mark == "":
        return None
    return MarkPriceEvent(
        symbol=str(row["instId"]),
        mark_price=Decimal(str(mark)),
        ts=int(row.get("ts") or row.get("uTime") or 0),
    )


def parse_message(msg: dict) -> list[KlineEvent] | list[MarkPriceEvent] | None:
    """Translate a single Bitget public WS frame to typed events.

    Returns None for non-data frames (subscribe ack, error, etc.).
    """
    arg = msg.get("arg") or {}
    channel = arg.get("channel", "")
    data = msg.get("data") or []
    if not data:
        return None

    if channel.startswith("candle"):
        # arg has instId AND channel. Derive interval from channel suffix.
        interval_suffix = channel.removeprefix("candle")
        interval = interval_suffix.lower()
        if interval not in _CANDLE_INTERVAL_CHANNEL:
            return None
        symbol = str(arg.get("instId", ""))
        # Last row = currently-forming bar; earlier rows = closed bars (rare on push).
        out: list[KlineEvent] = []
        for i, row in enumerate(data):
            closed = (i < len(data) - 1) or msg.get("action") == "snapshot"
            out.append(_parse_kline_row(symbol, interval, row, closed=closed))
        return out

    if channel == "ticker":
        marks: list[MarkPriceEvent] = []
        for row in data:
            m = _parse_ticker_row(row)
            if m is not None:
                marks.append(m)
        return marks if marks else None

    return None


class BitgetMarketDataStream:
    """Public WS reader. Subscribes to a fixed (symbol, interval) set + ticker.

    Yields raw events (KlineEvent / MarkPriceEvent) via two async iterators
    so consumers can route them separately. Both iterators share the same WS
    connection; first one started owns the read loop.
    """

    def __init__(
        self,
        *,
        kline_subs: Iterable[tuple[str, str]],  # [(symbol, interval), ...]
        ticker_subs: Iterable[str],             # [symbol, ...]
        paper: bool = True,
        queue_size: int = 1000,
    ) -> None:
        self._url = WS_PUBLIC_DEMO if paper else WS_PUBLIC_LIVE
        self._kline_subs = list(kline_subs)
        self._ticker_subs = list(ticker_subs)
        self._kline_q: asyncio.Queue[KlineEvent] = asyncio.Queue(maxsize=queue_size)
        self._ticker_q: asyncio.Queue[MarkPriceEvent] = asyncio.Queue(maxsize=queue_size)
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def _build_subscribe(self) -> dict:
        args: list[dict] = []
        for sym, iv in self._kline_subs:
            args.append({
                "instType": "USDT-FUTURES",
                "channel": _interval_to_channel(iv),
                "instId": sym,
            })
        for sym in self._ticker_subs:
            args.append({
                "instType": "USDT-FUTURES",
                "channel": "ticker",
                "instId": sym,
            })
        return {"op": "subscribe", "args": args}

    async def _consume(self) -> None:
        async with websockets.connect(self._url, ping_interval=_PING_INTERVAL_SEC) as ws:
            sub = self._build_subscribe()
            # Bitget recommends batching ≤50 subs per frame. Chunk if needed.
            args = sub["args"]
            CHUNK = 50
            for i in range(0, len(args), CHUNK):
                chunk_sub = {"op": "subscribe", "args": args[i:i + CHUNK]}
                await ws.send(json.dumps(chunk_sub))
            log.info("bitget market WS subscribed: %d kline + %d ticker",
                     len(self._kline_subs), len(self._ticker_subs))

            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=_PING_INTERVAL_SEC * 2)
                except asyncio.TimeoutError:
                    continue
                msg = json.loads(raw)
                events = parse_message(msg)
                if not events:
                    continue
                first = events[0]
                if isinstance(first, KlineEvent):
                    for e in events:
                        try:
                            self._kline_q.put_nowait(e)
                        except asyncio.QueueFull:
                            self._kline_q.get_nowait()
                            self._kline_q.put_nowait(e)
                elif isinstance(first, MarkPriceEvent):
                    for e in events:
                        try:
                            self._ticker_q.put_nowait(e)
                        except asyncio.QueueFull:
                            self._ticker_q.get_nowait()
                            self._ticker_q.put_nowait(e)

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set() and attempt < _RECONNECT_MAX_ATTEMPTS:
            try:
                await self._consume()
                attempt = 0
            except (websockets.exceptions.ConnectionClosed,
                    asyncio.TimeoutError, OSError) as exc:
                attempt += 1
                backoff = exponential_backoff(attempt)
                log.warning("bitget market WS disconnect (attempt %d): %s — retry in %.1fs",
                            attempt, exc, backoff)
                await asyncio.sleep(backoff)

    def stream_klines(self) -> AsyncIterator[KlineEvent]:
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        return self._iter(self._kline_q)

    def stream_mark_prices(self) -> AsyncIterator[MarkPriceEvent]:
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        return self._iter(self._ticker_q)

    async def _iter(self, q: asyncio.Queue) -> AsyncIterator:
        while not self._stop.is_set():
            try:
                yield await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

    async def aclose(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


# ── REST bootstrap ────────────────────────────────────────────────────────────

async def fetch_klines_rest(
    *,
    symbol: str,
    interval: str,
    limit: int = 100,
    paper: bool = True,
    base_url: str = REST_BASE_LIVE,
) -> list[KlineEvent]:
    """Fetch historical klines via REST (used for strategy warmup on startup)."""
    product_type = "USDT-FUTURES"
    # Bitget v2 candles param: 'granularity' = "1H", "5m", etc. (same as channel suffix).
    granularity = _CANDLE_INTERVAL_CHANNEL[interval].removeprefix("candle")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{base_url}/api/v2/mix/market/candles",
            params={
                "symbol": symbol,
                "productType": product_type,
                "granularity": granularity,
                "limit": str(limit),
            },
        )
    j = r.json()
    if str(j.get("code")) != "00000":
        raise RuntimeError(f"bitget candles {symbol}/{interval} failed: {j}")
    rows = j.get("data") or []
    return [_parse_kline_row(symbol, interval, row, closed=True) for row in rows]


async def bootstrap_history(
    *,
    symbols: Iterable[str],
    interval: str,
    limit: int = 100,
    paper: bool = True,
) -> dict[str, list[KlineEvent]]:
    """Parallel REST fetch for many symbols. Returns {symbol: [bars]}."""
    syms = list(symbols)
    results = await asyncio.gather(
        *(fetch_klines_rest(symbol=s, interval=interval, limit=limit, paper=paper)
          for s in syms),
        return_exceptions=True,
    )
    out: dict[str, list[KlineEvent]] = {}
    for s, r in zip(syms, results):
        if isinstance(r, Exception):
            log.warning("bitget bootstrap %s failed: %s", s, r)
            out[s] = []
        else:
            out[s] = r
    return out
