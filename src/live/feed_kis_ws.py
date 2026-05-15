"""KIS realtime WebSocket market-data feed (#227 follow-up).

Replaces ``KISMarketFeed`` (REST polling, 60s latency, rate-limited) with a
realtime WebSocket subscription to ``H0STCNT0`` (실시간 주식 체결가). One WS
connection multiplexes all subscribed symbols — the per-minute REST budget
ceiling that ``stagger=True`` was working around no longer applies.

Conforms to ``MarketDataFeed`` Protocol (connect / subscribe / __aiter__ /
aclose) so ``ShadowConfig.feed_mode='kis-ws'`` (out-of-scope follow-up) can
swap it in for ``KISMarketFeed`` with no consumer changes.

The execution-notification feed (``KISAsyncWebSocket``, H0STCNI9) is a
different stream and lives in ``src/brokers/kis/async_ws.py``. They share
the approval-key dance (REST POST /oauth2/Approval) but the subscribe
payload + the response-frame layout differ.

Reference: KIS Developers — 실시간 시세 (Web Socket) > 주식 체결가
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator, Iterable

import httpx
import websockets
import websockets.exceptions

from src.brokers.kis.auth import KISAuth
from src.brokers.kis.tr_ids import TR_ID_WS_KRX_TRADE
from src.live.types import Tick

logger = logging.getLogger(__name__)

_PAPER_WS_URL = "ws://ops.koreainvestment.com:31000"
_LIVE_WS_URL = "ws://ops.koreainvestment.com:21000"
_PAPER_REST_BASE = "https://openapivts.koreainvestment.com:29443"
_LIVE_REST_BASE = "https://openapi.koreainvestment.com:9443"


class KISWebSocketMarketFeed:
    """Realtime KRX trade-tick stream over a single WS connection.

    Usage::

        feed = KISWebSocketMarketFeed(["005930", "000660"], auth=..., app_key=...)
        await feed.connect()
        await feed.subscribe(["005930", "000660"])
        async for tick in feed:
            print(tick)

    The class is import-light — websockets/httpx are imported eagerly here so
    bench/test harnesses that don't activate KIS WS still pay the cost only
    once. Compare to ``KISMarketFeed`` which lazily imports the price client
    inside ``_iter`` — same trade-off.
    """

    def __init__(
        self,
        symbols: list[str],
        auth: KISAuth,
        app_key: str,
        *,
        paper: bool = True,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._symbols: list[str] = list(symbols)
        self._auth = auth
        self._app_key = app_key
        self._paper = paper
        self._ws_url = _PAPER_WS_URL if paper else _LIVE_WS_URL
        self._rest_base = _PAPER_REST_BASE if paper else _LIVE_REST_BASE
        self._http = http_client or httpx.AsyncClient(timeout=10.0, trust_env=False)
        self._owns_http = http_client is None
        self._approval_key: str | None = None
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._closed = False
        self._subscribed: set[str] = set()

    # ── Protocol surface ─────────────────────────────────────────────────

    async def connect(self) -> None:
        if not self._symbols:
            raise RuntimeError("KISWebSocketMarketFeed requires at least one symbol")
        self._approval_key = await self._get_approval_key()
        self._ws = await websockets.connect(self._ws_url)
        # Initial subscribe for the symbols passed at construction time.
        await self.subscribe(self._symbols)

    async def subscribe(self, symbols: Iterable[str]) -> None:
        if self._ws is None or self._approval_key is None:
            raise RuntimeError("call connect() before subscribe()")
        for sym in symbols:
            if sym in self._subscribed:
                continue
            msg = self._subscribe_msg(self._approval_key, sym)
            await self._ws.send(msg)
            self._subscribed.add(sym)
            if sym not in self._symbols:
                self._symbols.append(sym)

    def __aiter__(self) -> AsyncIterator[Tick]:
        return self._iter()

    async def aclose(self) -> None:
        self._closed = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._owns_http:
            await self._http.aclose()

    # ── Internals ────────────────────────────────────────────────────────

    async def _get_approval_key(self) -> str:
        resp = await self._http.post(
            f"{self._rest_base}/oauth2/Approval",
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "secretkey": self._auth._app_secret,
            },
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()["approval_key"]

    def _subscribe_msg(self, approval_key: str, symbol: str) -> str:
        return json.dumps({
            "header": {
                "approval_key": approval_key,
                "custtype": "P",
                "tr_type": "1",  # 1 = subscribe, 2 = unsubscribe
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": TR_ID_WS_KRX_TRADE,
                    "tr_key": symbol,
                }
            },
        })

    async def _iter(self) -> AsyncIterator[Tick]:
        if self._ws is None:
            raise RuntimeError("call connect() before iterating")
        try:
            async for raw in self._ws:
                if self._closed:
                    return
                tick = self._parse_message(raw)
                if tick is not None:
                    yield tick
        except websockets.exceptions.ConnectionClosed:
            if self._closed:
                return
            logger.warning("KISWebSocketMarketFeed: connection closed")
            return

    @staticmethod
    def _parse_message(raw: str) -> Tick | None:
        """Parse one frame from H0STCNT0.

        Subscribe ack frames start with ``{`` (JSON) — we ignore them.
        Trade frames are ``^``-delimited text with header ``0|H0STCNT0|<count>|<payload>``
        where ``<payload>`` is itself ``^``-delimited and the first three fields
        are: ``MKSC_SHRN_ISCD`` (symbol), ``STCK_CNTG_HOUR`` (HHMMSS),
        ``STCK_PRPR`` (current price). Field 12 = ``CNTG_VOL`` (해당 체결량).
        """
        if not raw or raw.startswith("{"):
            return None  # subscribe ack / pingpong / control frame
        parts = raw.split("|")
        if len(parts) < 4:
            return None
        tr_id = parts[1]
        if tr_id != TR_ID_WS_KRX_TRADE:
            return None
        payload = parts[3]
        fields = payload.split("^")
        if len(fields) < 13:
            return None
        try:
            symbol = fields[0]
            hms = fields[1]
            price = Decimal(fields[2]) if fields[2] else Decimal("0")
            qty = Decimal(fields[12]) if fields[12] else Decimal("0")
        except Exception as exc:
            logger.warning("KISWebSocketMarketFeed: parse error %s", exc)
            return None
        ts_now = datetime.now(timezone.utc).isoformat()
        return Tick(
            symbol=symbol,
            price=price,
            qty=qty,
            ts=ts_now,
            server_ts=hms,  # KST HHMMSS — caller may decode if needed
        )


# ────────────────────────────────────────────────────────────────────────────
# #231 S3-part2 — Multi-connection wrapper for >40 종목 (KOSPI200 200 종목 운영)
# ────────────────────────────────────────────────────────────────────────────


class MultiConnectionKISWebSocketFeed:
    """N 개의 KISWebSocketMarketFeed 를 동시 운영해 KIS WS 40 종목/conn 제한 회피.

    KOSPI200 200 종목 = 5 connection (batch_size=40). 각 batch 별 별도 asyncio
    task 로 tick stream + asyncio.Queue 로 merge — main loop 는 단일 iter.

    KIS WS subscription 한도 (account-level 동시 40 종목) 는 connection 별로
    독립이라 multi-connection 으로 분산 안전. 단 token 발급 rate-limit (분당
    20 회) 은 공통이라 KISAuth 인스턴스 공유 (단일 token, 모든 connection 재사용).

    Implements MarketDataFeed Protocol (connect / subscribe / __aiter__ / aclose).
    """

    BATCH_SIZE = 40  # KIS WS single connection max symbol subscriptions

    def __init__(
        self,
        symbols: list[str],
        auth: KISAuth,
        app_key: str,
        *,
        paper: bool = True,
        batch_size: int | None = None,
    ) -> None:
        self._symbols: list[str] = list(symbols)
        self._auth = auth
        self._app_key = app_key
        self._paper = paper
        self._batch_size = batch_size or self.BATCH_SIZE
        # Split into batches — share auth/app_key across all connections.
        self._feeds: list[KISWebSocketMarketFeed] = []
        for i in range(0, len(self._symbols), self._batch_size):
            batch = self._symbols[i : i + self._batch_size]
            self._feeds.append(KISWebSocketMarketFeed(
                batch, auth=auth, app_key=app_key, paper=paper,
            ))
        self._closed = False
        self._merge_tasks: list[asyncio.Task] = []
        self._queue: asyncio.Queue[Tick] = asyncio.Queue()

    async def connect(self) -> None:
        """Connect all underlying single-connection feeds concurrently."""
        await asyncio.gather(*(f.connect() for f in self._feeds))

    async def subscribe(self, symbols: list[str]) -> None:
        """Dynamic subscribe not supported — KOSPI200 운영은 초기 batch fixed.

        후속 sub-commit 에서 새 batch 추가 또는 기존 batch 빈자리에 append
        구현 가능.
        """
        raise NotImplementedError(
            "MultiConnectionKISWebSocketFeed: dynamic subscribe not yet "
            "supported. Pass full symbol list at construction time."
        )

    def __aiter__(self):
        return self._merge()

    async def _merge(self):
        """Merge tick streams from all batches into a single async iterator."""

        async def _relay(feed: KISWebSocketMarketFeed) -> None:
            try:
                async for tick in feed:
                    if self._closed:
                        return
                    await self._queue.put(tick)
            except Exception as exc:
                logger.warning(
                    "MultiConnectionKISWebSocketFeed: relay error feed=%s err=%s",
                    feed, exc,
                )

        self._merge_tasks = [
            asyncio.create_task(_relay(f), name=f"kis-ws-relay-{i}")
            for i, f in enumerate(self._feeds)
        ]
        try:
            while not self._closed:
                tick = await self._queue.get()
                yield tick
        finally:
            for t in self._merge_tasks:
                if not t.done():
                    t.cancel()

    async def aclose(self) -> None:
        self._closed = True
        for t in self._merge_tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(
            *(f.aclose() for f in self._feeds),
            return_exceptions=True,
        )

    # ── inspection helpers (단위 테스트 용) ─────────────────────────────────

    @property
    def n_connections(self) -> int:
        return len(self._feeds)

    @property
    def feeds(self) -> tuple:
        return tuple(self._feeds)
