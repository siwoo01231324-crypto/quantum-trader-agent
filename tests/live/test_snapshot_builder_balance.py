"""#238 Item 9 — real balance → snapshot equity wiring.

Item 8 made the orchestrator size against `snapshot["equity_usdt"]` /
`["equity_krw"]`, but no producer populated them (loop hardcoded a 100k
placeholder, equity_usdt absent) → the live path safely DROPPED every order
(inert). This injects the existing `AccountInfoProvider` (Binance available
USDT + KIS KRW cash, internally 15s-cached) so `build_snapshot` carries the
real venue balances. DI default None → byte-identical to pre-#238.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.live.snapshot_builder import SnapshotBuilder, SnapshotBuilderConfig
from src.live.types import Tick


def _tick(symbol: str) -> Tick:
    return Tick(symbol=symbol, price=Decimal("80000"), qty=Decimal("1"),
                ts="2026-05-17T01:00:00+00:00")


class _FakeProvider:
    """Mirrors AccountInfoProvider.fetch() shape."""

    def __init__(self, payload, *, raises: bool = False):
        self._payload = payload
        self._raises = raises
        self.calls = 0

    def fetch(self):
        self.calls += 1
        if self._raises:
            raise RuntimeError("balance fetch boom")
        return self._payload


def test_no_provider_is_bit_identical_placeholder():
    """Default (no balance_provider) → unchanged: config placeholder, no usdt."""
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None,
                        config=SnapshotBuilderConfig(equity_krw=100000.0))
    snap = b.build_snapshot(_tick("BTCUSDT"))
    assert snap["equity_krw"] == 100000.0
    assert snap.get("equity_usdt") in (None, 0.0)


def test_provider_populates_real_venue_equity():
    prov = _FakeProvider({
        "binance": {"ok": True, "available_usdt": 4610.903},
        "kis": {"ok": True, "cash_balance": 1_000_000},
    })
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None, balance_provider=prov)
    snap = b.build_snapshot(_tick("BTCUSDT"))
    assert snap["equity_usdt"] == 4610.903
    assert snap["equity_krw"] == 1_000_000.0


def test_provider_failure_falls_back_safely_no_crash():
    """A balance fetch error must not kill the per-tick hot loop."""
    prov = _FakeProvider(None, raises=True)
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None, balance_provider=prov,
                        config=SnapshotBuilderConfig(equity_krw=100000.0))
    snap = b.build_snapshot(_tick("BTCUSDT"))  # must not raise
    assert snap["equity_krw"] == 100000.0
    assert snap.get("equity_usdt") in (None, 0.0)


def test_binance_not_ok_yields_no_usdt_equity_safe_drop():
    """binance ok=False → equity_usdt absent/0 → item-8 conversion drops (safe)."""
    prov = _FakeProvider({
        "binance": {"ok": False, "error": "creds"},
        "kis": {"ok": True, "cash_balance": 500_000},
    })
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None, balance_provider=prov)
    snap = b.build_snapshot(_tick("BTCUSDT"))
    assert snap.get("equity_usdt") in (None, 0.0)
    assert snap["equity_krw"] == 500_000.0  # KIS side still real


def test_provider_called_per_snapshot_relies_on_internal_cache():
    """build_snapshot calls fetch() each tick; provider's own 15s cache
    (AccountInfoProvider) absorbs the rate — we do not re-cache here."""
    prov = _FakeProvider({
        "binance": {"ok": True, "available_usdt": 10.0},
        "kis": {"ok": False},
    })
    b = SnapshotBuilder(["BTCUSDT"], kis_client=None, balance_provider=prov)
    b.build_snapshot(_tick("BTCUSDT"))
    b.build_snapshot(_tick("BTCUSDT"))
    assert prov.calls == 2
