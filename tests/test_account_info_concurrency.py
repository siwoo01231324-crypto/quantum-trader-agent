"""#2 (prior-review MEDIUM) — AccountInfoProvider.fetch() check-then-act atomicity.

The 15s TTL cache `if cache and fresh: return cache` else refresh+store is now
hit concurrently by (a) the dashboard `/api/account/info` route on a worker
thread (`asyncio.to_thread(provider.fetch)`) and (b) the live pipeline via
`SnapshotBuilder._inject_real_equity` (same shared instance on the
attached/smoke path after #18). Unguarded check-then-act can: double/triple
the slow underlying REST on a cold miss, or return a torn/partial dict.

These tests pin the atomicity contract WITHOUT changing observable behaviour
(same 15s TTL, same per-broker fallback, same `_safe` ok:False, same shape):
  - N concurrent threads on a cold miss → underlying fetch bounded (at most 1)
  - every caller gets the SAME consistent {"kis","binance"} dict (no tear)
  - single-thread behaviour + TTL expiry unchanged
  - `_safe` exception path still yields ok:False
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.dashboard.account_info import AccountInfoProvider


def test_concurrent_cold_miss_calls_underlying_at_most_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N threads hitting a cold cache simultaneously, slow underlying fetch →
    the underlying KIS/Binance fetch runs at most once (check-then-act atomic),
    and every caller observes the identical consistent result dict."""
    provider = AccountInfoProvider(ttl_sec=30.0)
    calls = {"kis": 0, "binance": 0, "bitget": 0}
    barrier = threading.Barrier(8)

    def _kis(self) -> dict:
        calls["kis"] += 1
        time.sleep(0.15)  # slow REST
        return {"ok": True, "cano_masked": "1234****-01"}

    def _bnb(self) -> dict:
        calls["binance"] += 1
        time.sleep(0.15)
        return {"ok": True, "api_key_masked": "ab****cd"}

    def _bg(self) -> dict:
        calls["bitget"] += 1
        time.sleep(0.15)
        return {"ok": True, "api_key_masked": "bg****ef"}

    monkeypatch.setattr(AccountInfoProvider, "_fetch_kis", _kis)
    monkeypatch.setattr(AccountInfoProvider, "_fetch_binance", _bnb)
    monkeypatch.setattr(AccountInfoProvider, "_fetch_bitget", _bg)

    def _worker() -> dict:
        barrier.wait()  # release all 8 threads at once → maximize the race
        return provider.fetch()

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = [f.result() for f in [ex.submit(_worker) for _ in range(8)]]

    # Atomicity: the slow underlying must not be stampeded.
    assert calls["kis"] == 1, f"underlying KIS fetched {calls['kis']}x (stampede)"
    assert calls["binance"] == 1, f"underlying Binance fetched {calls['binance']}x"
    assert calls["bitget"] == 1, f"underlying Bitget fetched {calls['bitget']}x"
    # No torn/partial dict — every caller gets a complete consistent result.
    for r in results:
        assert set(r.keys()) == {"kis", "binance", "bitget"}
        assert r["kis"] == {"ok": True, "cano_masked": "1234****-01"}
        assert r["binance"] == {"ok": True, "api_key_masked": "ab****cd"}
    # All callers observe the SAME cached object (consistency).
    assert all(r is results[0] for r in results)


def test_single_thread_caching_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behaviour-preserving: TTL cache still collapses 2 calls into 1 fetch."""
    provider = AccountInfoProvider(ttl_sec=10.0)
    calls = {"kis": 0, "binance": 0}

    def _kis(self) -> dict:
        calls["kis"] += 1
        return {"ok": True}

    def _bnb(self) -> dict:
        calls["binance"] += 1
        return {"ok": True}

    monkeypatch.setattr(AccountInfoProvider, "_fetch_kis", _kis)
    monkeypatch.setattr(AccountInfoProvider, "_fetch_binance", _bnb)
    a = provider.fetch()
    b = provider.fetch()
    assert calls == {"kis": 1, "binance": 1}
    assert a is b


def test_ttl_expiry_triggers_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behaviour-preserving: a fetch after TTL elapses refreshes."""
    provider = AccountInfoProvider(ttl_sec=0.05)
    calls = {"n": 0}

    def _kis(self) -> dict:
        calls["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(AccountInfoProvider, "_fetch_kis", _kis)
    monkeypatch.setattr(
        AccountInfoProvider, "_fetch_binance", lambda self: {"ok": True}
    )
    provider.fetch()
    time.sleep(0.08)
    provider.fetch()
    assert calls["n"] == 2


def test_safe_error_path_still_ok_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_safe` still swallows and yields ok:False (unchanged)."""
    provider = AccountInfoProvider(ttl_sec=10.0)

    def _boom(self) -> dict:
        raise RuntimeError("kis down")

    monkeypatch.setattr(AccountInfoProvider, "_fetch_kis", _boom)
    monkeypatch.setattr(
        AccountInfoProvider, "_fetch_binance", lambda self: {"ok": True}
    )
    out = provider.fetch()
    assert out["kis"]["ok"] is False
    assert "RuntimeError" in out["kis"]["error"]
    assert out["binance"]["ok"] is True


def test_concurrent_dashboard_request_not_serialized_behind_slow_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A warm cache must serve concurrent callers WITHOUT serializing them
    behind the lock for the duration of a slow REST (the lock is not held
    across network I/O once the cache is warm)."""
    provider = AccountInfoProvider(ttl_sec=30.0)

    monkeypatch.setattr(
        AccountInfoProvider, "_fetch_kis",
        lambda self: (time.sleep(0.2), {"ok": True})[1],
    )
    monkeypatch.setattr(
        AccountInfoProvider, "_fetch_binance", lambda self: {"ok": True}
    )
    provider.fetch()  # warm the cache (pays the slow REST once)

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(f.result() for f in [ex.submit(provider.fetch) for _ in range(8)])
    elapsed = time.monotonic() - start
    # 8 warm-cache reads must be ~instant, not 8 * 0.2s serialized.
    assert elapsed < 0.2, f"warm reads serialized behind slow REST ({elapsed:.2f}s)"
