"""S2 (#231) — SnapshotBuilder.universe_quote_provider wiring.

Verifies that:
1. provider=None → ohlcv_history contains only live buffers (regression zero).
2. provider returning dict → ohlcv_history includes universe symbols merged.
3. provider throwing → graceful (logged warn, no exception propagation).
4. Cache TTL — second call within ttl reuses cache (provider invoked once).
5. live buffer takes precedence over universe cache on symbol collision.
"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from src.live.snapshot_builder import SnapshotBuilder, SnapshotBuilderConfig
from src.live.types import Tick


def _make_ohlcv(prices: list[float]) -> pd.DataFrame:
    n = len(prices)
    idx = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    return pd.DataFrame(
        {"open": prices, "high": prices, "low": prices,
         "close": prices, "volume": [1000.0] * n},
        index=idx,
    )


def _tick(sym: str = "005930") -> Tick:
    return Tick(
        symbol=sym, price=Decimal("70000"), qty=Decimal("100"),
        ts="2026-05-15T01:00:00+00:00",
    )


def test_no_provider_preserves_legacy_behavior():
    """provider=None — ohlcv_history is live buffers only (regression)."""
    builder = SnapshotBuilder(symbols=["005930"])
    snap = builder.build_snapshot(_tick("005930"))
    assert "005930" in snap["ohlcv_history"]
    assert len(snap["ohlcv_history"]) == 1


def test_provider_merges_universe_symbols():
    """provider returns dict → universe symbols available in ohlcv_history."""
    universe_data = {
        "035720": _make_ohlcv([60000.0, 60100.0]),
        "000660": _make_ohlcv([120000.0, 120500.0]),
        "207940": _make_ohlcv([800000.0, 805000.0]),
    }
    call_count = [0]
    def provider():
        call_count[0] += 1
        return universe_data

    builder = SnapshotBuilder(
        symbols=["005930"],
        universe_quote_provider=provider,
        universe_ttl_sec=300.0,
    )
    snap = builder.build_snapshot(_tick("005930"))

    # live buffer (005930) + universe (035720, 000660, 207940) merged.
    assert "005930" in snap["ohlcv_history"]
    assert "035720" in snap["ohlcv_history"]
    assert "000660" in snap["ohlcv_history"]
    assert "207940" in snap["ohlcv_history"]
    assert call_count[0] == 1


def test_provider_throw_graceful_hold():
    """Provider raising → cache stays empty, build_snapshot succeeds."""
    def broken_provider():
        raise RuntimeError("API timeout")

    builder = SnapshotBuilder(
        symbols=["005930"],
        universe_quote_provider=broken_provider,
    )
    # Must not raise — graceful hold via empty universe cache
    snap = builder.build_snapshot(_tick("005930"))
    assert "005930" in snap["ohlcv_history"]
    # Universe cache empty since provider failed
    assert len(snap["ohlcv_history"]) == 1


def test_cache_ttl_dedups_provider_calls():
    """Two build_snapshot calls within TTL → provider invoked once only."""
    call_count = [0]
    def provider():
        call_count[0] += 1
        return {"035720": _make_ohlcv([60000.0])}

    builder = SnapshotBuilder(
        symbols=["005930"],
        universe_quote_provider=provider,
        universe_ttl_sec=300.0,
    )
    builder.build_snapshot(_tick("005930"))
    builder.build_snapshot(_tick("005930"))
    builder.build_snapshot(_tick("005930"))
    # Only one fetch in 3 calls — TTL cache hit
    assert call_count[0] == 1


def test_live_buffer_overrides_universe_on_collision():
    """If same symbol exists in both, live buffer wins (newest tick price)."""
    # Universe data for 005930 with stale price
    universe_data = {"005930": _make_ohlcv([99999.0])}

    builder = SnapshotBuilder(
        symbols=["005930"],
        universe_quote_provider=lambda: universe_data,
    )
    snap = builder.build_snapshot(_tick("005930"))

    # Live tick (70000) overrides stale universe (99999) — last row close
    live_close = snap["ohlcv_history"]["005930"]["close"].iloc[-1]
    assert float(live_close) == 70000.0


def test_provider_returning_non_dict_silently_skipped():
    """If provider returns garbage (None, str, etc.), cache stays empty."""
    builder = SnapshotBuilder(
        symbols=["005930"],
        universe_quote_provider=lambda: None,  # bad return
    )
    snap = builder.build_snapshot(_tick("005930"))
    # Only live buffer
    assert "005930" in snap["ohlcv_history"]
    assert len(snap["ohlcv_history"]) == 1
