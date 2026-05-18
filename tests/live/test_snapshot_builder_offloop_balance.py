"""#3 (prior-review MEDIUM) — _inject_real_equity must not block the event loop.

`build_snapshot(tick)` runs synchronously in the live consumer coroutine on
the event-loop thread (`src/live/loop.py`). `_inject_real_equity` previously
called `self._balance_provider.fetch()` inline; on a 15s-cache MISS that
blocks the tick loop on KIS+Binance REST — the exact contention the #18 KIS
root-cause cares about.

Fix contract pinned here (WHERE the fetch runs changes, not WHICH values):
  - the loop refreshes the provider OFF the event-loop thread, once per tick,
    BEFORE the sync build_snapshot (via `SnapshotBuilder.refresh_balance`,
    which the loop wraps in `asyncio.to_thread`);
  - `_inject_real_equity` only ever does a NON-BLOCKING cached read
    (`provider.peek()`), never inline REST, when the provider exposes it;
  - the equity values applied are byte-identical to the pre-fix overlay;
  - last-known-good + `last_equity_status` semantics preserved;
  - `balance_provider is None` (default) → byte-identical (no thread hop,
    no peek, early return).
"""
from __future__ import annotations

import asyncio
import threading
from decimal import Decimal

from src.live.snapshot_builder import SnapshotBuilder, SnapshotBuilderConfig
from src.live.types import Tick


def _tick(symbol: str = "BTCUSDT") -> Tick:
    return Tick(symbol=symbol, price=Decimal("80000"), qty=Decimal("1"),
                ts="2026-05-17T01:00:00+00:00")


class _PeekProvider:
    """Mirrors AccountInfoProvider: fetch() = (slow) refresh + cache;
    peek() = non-blocking cached read (None until first fetch)."""

    def __init__(self, payload):
        self._payload = payload
        self._cache = None
        self.fetch_calls = 0
        self.peek_calls = 0
        self.fetch_threads: list[str] = []

    def fetch(self):
        self.fetch_calls += 1
        self.fetch_threads.append(threading.current_thread().name)
        self._cache = self._payload
        return self._cache

    def peek(self):
        self.peek_calls += 1
        return self._cache


def test_inject_uses_nonblocking_peek_not_inline_fetch():
    """With a provider exposing peek(), _inject_real_equity must read via
    peek() and must NOT call the (potentially blocking) fetch() itself."""
    prov = _PeekProvider({
        "binance": {"ok": True, "available_usdt": 4610.903},
        "kis": {"ok": True, "cash_balance": 1_000_000},
    })
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None, balance_provider=prov)
    # Pre-warm off-loop (what the loop does before the sync build_snapshot).
    b.refresh_balance()
    assert prov.fetch_calls == 1
    snap = b.build_snapshot(_tick())
    # build_snapshot's _inject_real_equity must NOT have called fetch().
    assert prov.fetch_calls == 1, "_inject_real_equity called blocking fetch()"
    assert prov.peek_calls >= 1, "_inject_real_equity did not use peek()"
    # Values applied are byte-identical to the pre-fix overlay.
    assert snap["equity_usdt"] == 4610.903
    assert snap["equity_krw"] == 1_000_000.0


def test_event_loop_thread_never_runs_provider_fetch():
    """End-to-end seam: refresh_balance runs off-loop (asyncio.to_thread);
    the sync build_snapshot on the loop thread only peeks. The provider's
    fetch() must never be invoked from the event-loop thread."""
    prov = _PeekProvider({
        "binance": {"ok": True, "available_usdt": 50.0},
        "kis": {"ok": False},
    })
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None, balance_provider=prov)

    async def _one_tick():
        loop_thread = threading.current_thread().name
        # Loop pre-warms the provider OFF the event-loop thread.
        await asyncio.to_thread(b.refresh_balance)
        # Sync build_snapshot runs on the event-loop thread.
        snap = b.build_snapshot(_tick())
        assert loop_thread not in prov.fetch_threads, (
            f"provider.fetch() ran on the event-loop thread {loop_thread}"
        )
        return snap

    snap = asyncio.run(_one_tick())
    assert snap["equity_usdt"] == 50.0


def test_peek_returns_none_before_refresh_is_safe_no_crash():
    """If peek() has nothing yet (no off-loop refresh ran), _inject must not
    raise into the hot loop and must leave the placeholder (safe drop)."""
    prov = _PeekProvider({"binance": {"ok": True, "available_usdt": 9.0},
                          "kis": {"ok": True, "cash_balance": 1}})
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None, balance_provider=prov,
                        config=SnapshotBuilderConfig(equity_krw=100000.0))
    snap = b.build_snapshot(_tick())  # no refresh_balance() yet
    assert snap["equity_krw"] == 100000.0
    assert snap.get("equity_usdt") in (None, 0.0)


def test_no_provider_is_byte_identical_no_thread_hop():
    """balance_provider=None (default) → refresh_balance is a no-op and
    build_snapshot is byte-identical to pre-#3 (early return, no peek)."""
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None,
                        config=SnapshotBuilderConfig(equity_krw=100000.0))
    b.refresh_balance()  # must be a harmless no-op
    snap = b.build_snapshot(_tick())
    assert snap["equity_krw"] == 100000.0
    assert snap.get("equity_usdt") in (None, 0.0)
    assert b.last_equity_status == {}


def test_last_known_good_preserved_with_peek_provider():
    """Off-loop refresh + peek path must preserve last-known-good: once a
    venue's real equity is observed, a later transient ok:False must NOT
    regress the snapshot to the placeholder."""
    seq = [
        {"kis": {"ok": True, "cash_balance": 9734720}, "binance": {"ok": False}},
        {"kis": {"ok": False, "error": "EGW00201"}, "binance": {"ok": False}},
    ]

    class _SeqPeek:
        def __init__(self):
            self.i = 0
            self._cache = None

        def fetch(self):
            self._cache = seq[min(self.i, len(seq) - 1)]
            self.i += 1
            return self._cache

        def peek(self):
            return self._cache

    b = SnapshotBuilder(["005930"], kis_client=None, balance_provider=_SeqPeek(),
                        config=SnapshotBuilderConfig(equity_krw=100000.0))
    b.refresh_balance()
    s1 = b.build_snapshot(_tick("005930"))
    assert s1["equity_krw"] == 9734720.0
    b.refresh_balance()  # transient failure now in cache
    s2 = b.build_snapshot(_tick("005930"))
    assert s2["equity_krw"] == 9734720.0, "regressed to placeholder on transient fail"
    assert b.last_equity_status["kis"]["ok"] is False


def test_provider_without_peek_still_works_via_fetch_fallback():
    """Back-compat: a provider that only implements fetch() (the existing
    test fakes / any legacy provider) still overlays equity. fetch() is the
    fallback read when peek() is unavailable."""
    class _FetchOnly:
        def __init__(self):
            self.calls = 0

        def fetch(self):
            self.calls += 1
            return {"binance": {"ok": True, "available_usdt": 7.0},
                    "kis": {"ok": False}}

    prov = _FetchOnly()
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None, balance_provider=prov)
    snap = b.build_snapshot(_tick())
    assert snap["equity_usdt"] == 7.0
    assert prov.calls >= 1
