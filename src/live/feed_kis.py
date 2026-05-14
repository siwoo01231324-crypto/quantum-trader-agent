"""KIS REST 1-minute polling market data feed (#177).

Phase 1-2 stand-in for the missing realtime KIS WS market feed (`H0STCNT0`).
Polls `inquire-time-itemchartprice` (FHKST03010200) every `poll_interval_sec`
and yields a Tick whenever a new 1m bar appears for a registered symbol.

Trade-offs
----------
- Latency: up to `poll_interval_sec` (default 60s) — acceptable for the
  registered strategies whose smallest bar is 15 min (MomoKisV1) or 4h.
- Quota: one API call per symbol per poll cycle. With 30 KRX symbols the
  budget is 30 reqs/min — well under the KIS 2 rps paper limit.
- Off-hours: when `is_market_open()` is False (weekend, holiday, before
  09:00 KST, after 15:30 KST), the feed sleeps without making API calls.

Replacement plan: a future issue (#xxx) wires the realtime KIS WS feed
once the broker has subscription support; this REST polling implementation
remains as a fallback / shadow-mode validator.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Any, AsyncIterator, Iterable

from src.live.types import Tick
from universe.krx_calendar import KST, is_krx_holiday

logger = logging.getLogger(__name__)

# KRX regular session window (KST). Single-auction periods are intentionally
# excluded from the polling window — they emit no per-minute bars anyway.
_KRX_OPEN = time(9, 0)
_KRX_CLOSE = time(15, 30)


def is_krx_market_open(now_utc: datetime | None = None) -> bool:
    """Return True iff *now_utc* (default: current UTC) maps to an open KRX session."""
    now = now_utc or datetime.now(timezone.utc)
    kst_now = now.astimezone(KST)
    if kst_now.weekday() >= 5:
        return False
    if is_krx_holiday(kst_now.date()):
        return False
    return _KRX_OPEN <= kst_now.time() <= _KRX_CLOSE


class KISMarketFeed:
    """REST polling feed conforming to the `MarketDataFeed` Protocol.

    The constructor accepts a *client* duck-typing the `KISClient._get` method,
    so tests can inject a stub without setting up real auth.
    """

    def __init__(
        self,
        symbols: list[str],
        client: Any,
        *,
        poll_interval_sec: float = 60.0,
        interval_min: str = "1",
        market_open_check: bool = True,
        stagger: bool = False,
        max_qpm: int | None = None,
    ) -> None:
        """KIS REST polling feed.

        Args:
            stagger: when True, the per-cycle loop spreads the N symbol fetches
                evenly across ``poll_interval_sec`` (sleep ``poll_interval_sec/N``
                between calls) instead of bursting all N back-to-back at the
                start of each minute. Required for #227 universe-scale runs
                (350 KRX symbols / 60s = 5.83 calls/sec sustained — the burst
                form would breach KIS ``EGW00201`` rate limits.)
            max_qpm: hard ceiling on REST calls per minute. When set, the cycle
                aborts after ``max_qpm`` symbols and resumes the remainder on
                the next cycle (round-robin offset preserved). ``None`` (default)
                means no ceiling.
        """
        self._symbols: list[str] = list(symbols)
        self._client = client
        self._poll_interval_sec = float(poll_interval_sec)
        self._interval_min = interval_min
        self._market_open_check = market_open_check
        self._stagger = bool(stagger)
        self._max_qpm = max_qpm
        self._cycle_offset = 0  # for round-robin under max_qpm
        self._last_bar_key: dict[str, tuple[str, str]] = {}
        self._closed = False

    # ── Protocol surface ─────────────────────────────────────────────────

    async def connect(self) -> None:
        if not self._symbols:
            raise RuntimeError("KISMarketFeed requires at least one symbol")

    async def subscribe(self, symbols: Iterable[str]) -> None:
        for s in symbols:
            if s not in self._symbols:
                self._symbols.append(s)

    def __aiter__(self) -> AsyncIterator[Tick]:
        return self._iter()

    async def aclose(self) -> None:
        self._closed = True

    # ── Internals ────────────────────────────────────────────────────────

    async def _iter(self) -> AsyncIterator[Tick]:
        # Local import to avoid heavy KIS deps at import time of src.live.loop.
        from src.brokers.kis.price_client import fetch_intraday_ohlcv_raw

        while not self._closed:
            if self._market_open_check and not is_krx_market_open():
                # Sleep then re-check; emit nothing.
                await asyncio.sleep(self._poll_interval_sec)
                continue

            symbols = list(self._symbols)
            n = len(symbols)
            # #227 S5: under max_qpm, fetch a rotating window of size max_qpm
            # so every symbol is eventually covered across multiple cycles.
            if self._max_qpm is not None and n > self._max_qpm:
                start = self._cycle_offset % n
                window = symbols[start:start + self._max_qpm]
                if len(window) < self._max_qpm:
                    window += symbols[: self._max_qpm - len(window)]
                self._cycle_offset = (start + self._max_qpm) % n
                cycle_symbols = window
            else:
                cycle_symbols = symbols

            stagger_delay = 0.0
            if self._stagger and len(cycle_symbols) > 0:
                stagger_delay = self._poll_interval_sec / len(cycle_symbols)

            for idx, symbol in enumerate(cycle_symbols):
                if self._closed:
                    return
                today = datetime.now(KST).strftime("%Y%m%d")
                try:
                    bars = await asyncio.to_thread(
                        fetch_intraday_ohlcv_raw,
                        self._client, symbol, today,
                        interval=self._interval_min,
                    )
                except Exception as exc:
                    logger.warning(
                        "KISMarketFeed.fetch_failed symbol=%s error=%s",
                        symbol, exc,
                    )
                    if stagger_delay > 0 and idx < len(cycle_symbols) - 1:
                        await asyncio.sleep(stagger_delay)
                    continue
                if bars:
                    latest = bars[-1]
                    key = (latest.date, latest.time)
                    if self._last_bar_key.get(symbol) != key:
                        self._last_bar_key[symbol] = key
                        yield self._bar_to_tick(symbol, latest)
                if stagger_delay > 0 and idx < len(cycle_symbols) - 1:
                    await asyncio.sleep(stagger_delay)

            if not self._stagger:
                await asyncio.sleep(self._poll_interval_sec)

    @staticmethod
    def _bar_to_tick(symbol: str, bar: Any) -> Tick:
        kst_dt = datetime.strptime(
            f"{bar.date}{bar.time}", "%Y%m%d%H%M%S",
        ).replace(tzinfo=KST)
        return Tick(
            symbol=symbol,
            price=Decimal(str(bar.close)),
            qty=Decimal(str(bar.volume)),
            ts=datetime.now(timezone.utc).isoformat(),
            server_ts=kst_dt.astimezone(timezone.utc).isoformat(),
        )


class MockReplayFeed:
    """Deterministic feed that replays a pre-canned tick list.

    Used by smoke tests and `qta.exe --feed mock` for off-hours runs where
    real KIS/Binance feeds would block forever waiting for a market session.
    """

    def __init__(self, ticks: list[Tick], *, gap_sec: float = 0.0) -> None:
        self._ticks: list[Tick] = list(ticks)
        self._gap_sec = float(gap_sec)
        self._closed = False

    async def connect(self) -> None:
        return None

    async def subscribe(self, symbols: Iterable[str]) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[Tick]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[Tick]:
        for tick in self._ticks:
            if self._closed:
                return
            if self._gap_sec > 0:
                await asyncio.sleep(self._gap_sec)
            yield tick

    async def aclose(self) -> None:
        self._closed = True
