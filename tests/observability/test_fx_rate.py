"""Tests for src/observability/fx_rate.py — TDD Red→Green.

Primary source: requests + ExchangeRate-API (yfinance not in deps).
"""
from __future__ import annotations

import time
import pytest

from src.observability.fx_rate import FxRateCache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fetch_mock(return_value, *, raise_exc=None):
    calls = []

    def mock_fetch():
        calls.append(1)
        if raise_exc is not None:
            raise raise_exc
        return return_value

    mock_fetch.calls = calls
    return mock_fetch


# ---------------------------------------------------------------------------
# Test 1: cache hit — fetch is NOT called a second time within TTL
# ---------------------------------------------------------------------------

def test_cache_hit_no_refetch(monkeypatch):
    cache = FxRateCache(ttl_sec=60)
    mock = _make_fetch_mock(1350.0)
    monkeypatch.setattr(cache, "_fetch", mock)

    first = cache.get()
    second = cache.get()

    assert first == 1350.0
    assert second == 1350.0
    assert len(mock.calls) == 1, "fetch should only be called once within TTL"


# ---------------------------------------------------------------------------
# Test 2: TTL expiry — fetch is called again after TTL expires
# ---------------------------------------------------------------------------

def test_ttl_expiry_triggers_refetch(monkeypatch):
    cache = FxRateCache(ttl_sec=0)  # TTL=0 → always expired
    call_count = [0]

    def mock_fetch():
        call_count[0] += 1
        return 1360.0 + call_count[0]

    monkeypatch.setattr(cache, "_fetch", mock_fetch)

    first = cache.get()
    second = cache.get()

    assert call_count[0] == 2, "fetch must be called on every get when TTL=0"
    assert first != second


# ---------------------------------------------------------------------------
# Test 3: fetch failure — returns stale value with warning logged
# ---------------------------------------------------------------------------

def test_fetch_failure_returns_stale(monkeypatch, caplog):
    import logging
    cache = FxRateCache(ttl_sec=0)

    # Pre-populate stale value by a successful fetch
    success_mock = _make_fetch_mock(1340.0)
    monkeypatch.setattr(cache, "_fetch", success_mock)
    cache.get()  # populate cache

    # Now mock fetch to fail
    fail_mock = _make_fetch_mock(None, raise_exc=RuntimeError("network error"))
    monkeypatch.setattr(cache, "_fetch", fail_mock)

    with caplog.at_level(logging.WARNING, logger="src.observability.fx_rate"):
        result = cache.get()

    assert result == 1340.0, "should return last successful value on fetch failure"
    assert any("stale" in r.message.lower() or "warning" in r.levelname.lower()
               for r in caplog.records), "should log a warning on stale fallback"


# ---------------------------------------------------------------------------
# Test 4: 24h stale — None returned to signal metric suppression
# ---------------------------------------------------------------------------

def test_24h_stale_returns_none(monkeypatch):
    cache = FxRateCache(ttl_sec=0)

    # Pre-populate with a value timestamped 25 hours ago
    success_mock = _make_fetch_mock(1320.0)
    monkeypatch.setattr(cache, "_fetch", success_mock)
    cache.get()  # sets _last_success_ts

    # Wind back the last-success timestamp by 25 hours
    cache._last_success_ts -= 25 * 3600

    # Now make fetch fail so we rely on stale
    fail_mock = _make_fetch_mock(None, raise_exc=RuntimeError("timeout"))
    monkeypatch.setattr(cache, "_fetch", fail_mock)

    result = cache.get()
    assert result is None, "should return None when stale data is older than 24 hours"


# ---------------------------------------------------------------------------
# Test 5: fresh fetch success resets stale clock
# ---------------------------------------------------------------------------

def test_fresh_fetch_resets_stale_clock(monkeypatch):
    cache = FxRateCache(ttl_sec=0)

    call_count = [0]

    def mock_fetch():
        call_count[0] += 1
        return 1370.0

    monkeypatch.setattr(cache, "_fetch", mock_fetch)
    result = cache.get()

    assert result == 1370.0
    assert cache._last_success_ts is not None
    # clock should be recent (within 5 seconds of now)
    assert abs(cache._last_success_ts - time.monotonic()) < 5


# ---------------------------------------------------------------------------
# Test 6: age_seconds property increases over time (basic sanity)
# ---------------------------------------------------------------------------

def test_age_seconds_increases(monkeypatch):
    cache = FxRateCache(ttl_sec=300)
    mock = _make_fetch_mock(1355.0)
    monkeypatch.setattr(cache, "_fetch", mock)

    cache.get()  # populate

    # Artificially age the last_success_ts
    cache._last_success_ts -= 10

    assert cache.age_seconds >= 10, "age_seconds should reflect elapsed time since last success"
