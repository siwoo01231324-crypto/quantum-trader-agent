"""Binance USDM Futures public market-data WS + REST kline bootstrap.

No auth required (public endpoints). Used by ``scripts/airborne_alert_daemon.py``
to stream confirmed 1h/5m klines + markPrice ticks across a top-N USDT-perp
universe.

Surfaces:
- :class:`KlineEvent` / :class:`MarkPriceEvent` — parsed event dataclasses
- :func:`parse_combined_message` — pure parser (test-friendly)
- :func:`build_combined_stream_url` — URL builder
- :class:`BinanceMarketDataStream` — async iterator with reconnect
- :func:`fetch_klines_rest` — historical bootstrap

The user-data WebSocket lives in ``async_ws.py`` (listenKey + fills); this
module is intentionally separate because it has no auth, no listenKey, and a
different event vocabulary.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Union

import httpx
import pandas as pd
import websockets
import websockets.exceptions

log = logging.getLogger(__name__)

WS_BASE_LIVE = "wss://fstream.binance.com"
WS_BASE_TESTNET = "wss://stream.binancefuture.com"
REST_BASE_LIVE = "https://fapi.binance.com"
REST_BASE_TESTNET = "https://testnet.binancefuture.com"


@dataclass(frozen=True)
class KlineEvent:
    symbol: str           # uppercase, e.g. "BTCUSDT"
    interval: str         # "1h", "5m", ...
    open_time: int        # ms epoch (bar open)
    close_time: int       # ms epoch (bar close)
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool       # True only when k.x — bar finalized


@dataclass(frozen=True)
class MarkPriceEvent:
    symbol: str
    mark_price: float
    funding_rate: float
    next_funding_time: int  # ms epoch
    event_time: int         # ms epoch


MarketEvent = Union[KlineEvent, MarkPriceEvent]


def parse_combined_message(msg: dict) -> KlineEvent | list[MarkPriceEvent] | None:
    """Pure parser for combined-stream messages.

    Returns:
      - :class:`KlineEvent` for ``<symbol>@kline_<interval>`` streams.
      - ``list[MarkPriceEvent]`` for ``!markPrice@arr@1s`` (whose ``data`` is a
        full-universe array — caller decides which symbols to consume).
      - ``None`` for unknown event types or malformed payloads.
    """
    stream = msg.get("stream", "")
    data = msg.get("data")

    if isinstance(data, list) and stream.endswith("@arr@1s"):
        out: list[MarkPriceEvent] = []
        for d in data:
            if not isinstance(d, dict) or d.get("e") != "markPriceUpdate":
                continue
            try:
                out.append(MarkPriceEvent(
                    symbol=str(d["s"]),
                    mark_price=float(d["p"]),
                    funding_rate=float(d.get("r", 0.0) or 0.0),
                    next_funding_time=int(d.get("T", 0) or 0),
                    event_time=int(d.get("E", 0) or 0),
                ))
            except (KeyError, ValueError, TypeError):
                continue
        return out

    if isinstance(data, dict) and data.get("e") == "kline":
        k = data.get("k", {})
        if not isinstance(k, dict):
            return None
        try:
            return KlineEvent(
                symbol=str(data["s"]),
                interval=str(k["i"]),
                open_time=int(k["t"]),
                close_time=int(k["T"]),
                open=float(k["o"]),
                high=float(k["h"]),
                low=float(k["l"]),
                close=float(k["c"]),
                volume=float(k.get("v", 0.0) or 0.0),
                is_closed=bool(k.get("x", False)),
            )
        except (KeyError, ValueError, TypeError):
            return None
    return None


def build_combined_stream_url(
    base_url: str,
    *,
    symbols: list[str],
    intervals: list[str],
    include_mark_price_arr: bool = True,
) -> str:
    """Build ``/stream?streams=...`` URL for combined kline + markPrice subscription.

    Symbol case is lowered for the stream name (Binance convention). ``intervals``
    accepts the Binance kline interval vocabulary ("1m", "5m", "1h", ...).
    """
    streams: list[str] = []
    for sym in symbols:
        s = sym.lower()
        for iv in intervals:
            streams.append(f"{s}@kline_{iv}")
    if include_mark_price_arr:
        streams.append("!markPrice@arr@1s")
    joined = "/".join(streams)
    return f"{base_url.rstrip('/')}/stream?streams={joined}"


class BinanceMarketDataStream:
    """Async iterator over combined market-data events with auto-reconnect.

    Args:
        base_url: WS base. Default = ``WS_BASE_LIVE``. Use ``WS_BASE_TESTNET``
            for testnet.
        symbols: list of uppercase Binance symbols (e.g. ``["BTCUSDT", ...]``).
        intervals: kline intervals to subscribe (default ``["1h", "5m"]``).
        include_mark_price_arr: subscribe to ``!markPrice@arr@1s`` if True.
        max_reconnect_attempts: stop reconnecting after this many failures.
    """

    def __init__(
        self,
        *,
        symbols: list[str],
        intervals: tuple[str, ...] | list[str] = ("1h", "5m"),
        base_url: str = WS_BASE_LIVE,
        include_mark_price_arr: bool = True,
        max_reconnect_attempts: int = 20,
    ) -> None:
        if not symbols:
            raise ValueError("symbols must not be empty")
        self._base_url = base_url
        self._symbols = list(symbols)
        self._intervals = list(intervals)
        self._include_mark = include_mark_price_arr
        self._max_reconnect = max_reconnect_attempts
        self._closed = False

    @property
    def stream_count(self) -> int:
        return len(self._symbols) * len(self._intervals) + (1 if self._include_mark else 0)

    async def stream(self) -> AsyncIterator[MarketEvent]:
        url = build_combined_stream_url(
            self._base_url,
            symbols=self._symbols,
            intervals=self._intervals,
            include_mark_price_arr=self._include_mark,
        )
        attempt = 0
        while not self._closed:
            try:
                async with websockets.connect(url, max_size=2**22) as ws:
                    log.info("market WS connected (%d streams)", self.stream_count)
                    attempt = 0  # reset on success
                    async for raw in ws:
                        if self._closed:
                            return
                        try:
                            msg = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            log.warning("unparseable WS message: %r", str(raw)[:200])
                            continue
                        parsed = parse_combined_message(msg)
                        if parsed is None:
                            continue
                        if isinstance(parsed, list):
                            for ev in parsed:
                                yield ev
                        else:
                            yield parsed
            except asyncio.CancelledError:
                raise
            except (websockets.exceptions.ConnectionClosed, OSError) as exc:
                log.warning("market WS disconnect: %s — reconnecting (attempt %d)", exc, attempt)
            except Exception as exc:
                log.warning("market WS error: %s — reconnecting (attempt %d)", exc, attempt)

            if self._closed:
                return
            attempt += 1
            if attempt > self._max_reconnect:
                log.error("market WS reconnect exceeded max attempts — giving up")
                return
            backoff = min(2 ** min(attempt, 6), 60.0)
            await asyncio.sleep(backoff)

    async def close(self) -> None:
        self._closed = True


async def fetch_klines_rest(
    *,
    symbol: str,
    interval: str,
    limit: int = 100,
    base_url: str = REST_BASE_LIVE,
    client: httpx.AsyncClient | None = None,
) -> pd.DataFrame:
    """Fetch historical klines via REST ``/fapi/v1/klines`` (max 1500/req).

    Returns DataFrame indexed by UTC datetime with columns
    ``open, high, low, close, volume``. Used for bootstrapping per-symbol
    candle history before WS subscription so signal evaluators have enough
    bars for BB(20).
    """
    url = f"{base_url.rstrip('/')}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": str(limit)}
    if client is None:
        async with httpx.AsyncClient(timeout=10.0) as c:
            resp = await c.get(url, params=params)
    else:
        resp = await client.get(url, params=params, timeout=10.0)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame({
        "open_time": [int(r[0]) for r in rows],
        "open": [float(r[1]) for r in rows],
        "high": [float(r[2]) for r in rows],
        "low": [float(r[3]) for r in rows],
        "close": [float(r[4]) for r in rows],
        "volume": [float(r[5]) for r in rows],
    })
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.drop(columns=["open_time"], inplace=True)
    return df


async def bootstrap_history(
    *,
    symbols: list[str],
    intervals: tuple[str, ...] | list[str] = ("1h", "5m"),
    limit_per_interval: dict[str, int] | None = None,
    base_url: str = REST_BASE_LIVE,
    concurrency: int = 8,
) -> dict[str, dict[str, pd.DataFrame]]:
    """Bootstrap per-symbol per-interval kline history before WS subscription.

    Returns ``{symbol: {interval: DataFrame}}``. Honors ``concurrency`` via an
    ``asyncio.Semaphore`` to stay under Binance REST weight limits.
    """
    limits = limit_per_interval or {iv: 100 for iv in intervals}
    sem = asyncio.Semaphore(max(1, concurrency))
    result: dict[str, dict[str, pd.DataFrame]] = {s: {} for s in symbols}

    async with httpx.AsyncClient(timeout=10.0) as client:
        async def _one(sym: str, iv: str) -> None:
            async with sem:
                df = await fetch_klines_rest(
                    symbol=sym, interval=iv,
                    limit=limits.get(iv, 100),
                    base_url=base_url, client=client,
                )
            result[sym][iv] = df

        await asyncio.gather(*[_one(s, iv) for s in symbols for iv in intervals])
    return result


__all__ = [
    "WS_BASE_LIVE", "WS_BASE_TESTNET",
    "REST_BASE_LIVE", "REST_BASE_TESTNET",
    "KlineEvent", "MarkPriceEvent", "MarketEvent",
    "parse_combined_message", "build_combined_stream_url",
    "BinanceMarketDataStream",
    "fetch_klines_rest", "bootstrap_history",
]
