"""Unit tests for src.portfolio.binance_top_dynamic — top-N universe cache."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.portfolio import binance_top_dynamic as mod


@pytest.fixture(autouse=True)
def _clear_cache():
    """매 테스트 사이 process-global 캐시 초기화."""
    mod.clear_cache()
    yield
    mod.clear_cache()


class TestGetTopN:
    def test_invalid_n_raises(self):
        with pytest.raises(ValueError, match="n > 0"):
            mod.get_top_n_symbols(0)
        with pytest.raises(ValueError):
            mod.get_top_n_symbols(-1)

    def test_fresh_fetch_caches(self, monkeypatch):
        """첫 호출 → async fetch → 두번째는 캐시 hit."""
        async def fake_fetch(_n):
            return [f"SYM{i}USDT" for i in range(_n)]

        with patch.object(mod, "_refresh_async", side_effect=fake_fetch):
            symbols = mod.get_top_n_symbols(100)
            assert len(symbols) == 100
            assert symbols[0] == "SYM0USDT"

            # 두번째 호출 — fetch 안 일어남
            with patch.object(mod, "_refresh_async") as not_called:
                symbols2 = mod.get_top_n_symbols(100)
                not_called.assert_not_called()
            assert symbols2 == symbols

    def test_different_n_separate_cache(self):
        async def fake_fetch(n):
            return [f"SYM{i}" for i in range(n)]

        with patch.object(mod, "_refresh_async", side_effect=fake_fetch):
            top30 = mod.get_top_n_symbols(30)
            top100 = mod.get_top_n_symbols(100)
            assert len(top30) == 30
            assert len(top100) == 100

    def test_stale_after_ttl(self, monkeypatch):
        """5분 + 1초 지나면 cache stale → 재 fetch."""
        async def fake_fetch(_n):
            return ["BTCUSDT", "ETHUSDT"]

        with patch.object(mod, "_refresh_async", side_effect=fake_fetch):
            mod.get_top_n_symbols(2)
            # 캐시 시각을 6분 전으로 강제
            with mod._state_lock:
                mod._cache_at[2] = datetime.now(timezone.utc) - timedelta(minutes=6)
            # 다시 호출 → re-fetch (mock 이 다시 호출됨)
            with patch.object(mod, "_refresh_async", side_effect=fake_fetch) as again:
                mod.get_top_n_symbols(2)
                again.assert_called_once()


class TestFallback:
    def test_fetch_failure_falls_back_to_top30(self):
        async def boom(_n):
            raise RuntimeError("network unreachable")

        with patch.object(mod, "_refresh_async", side_effect=boom):
            symbols = mod.get_top_n_symbols(100)
        from src.portfolio.binance_universe import BINANCE_USDT_TOP30
        assert symbols == list(BINANCE_USDT_TOP30)
        # 캐시에 fallback 저장 안 됨 — 다음 호출에서 재시도
        assert mod._fresh(100) is None

    def test_empty_response_treated_as_failure(self):
        async def empty(_n):
            return []

        with patch.object(mod, "_refresh_async", side_effect=empty):
            symbols = mod.get_top_n_symbols(50)
        from src.portfolio.binance_universe import BINANCE_USDT_TOP30
        assert symbols == list(BINANCE_USDT_TOP30)


class TestThreadSafety:
    def test_single_flight_under_concurrent_fetch(self, monkeypatch):
        """두 thread 동시 호출 — fetch 1회만 일어남 (single-flight)."""
        import threading
        import time

        fetch_calls: list = []

        async def slow_fetch(_n):
            fetch_calls.append("called")
            await __import__("asyncio").sleep(0.05)
            return ["A", "B"]

        with patch.object(mod, "_refresh_async", side_effect=slow_fetch):
            results: list = [None, None]

            def worker(idx):
                results[idx] = mod.get_top_n_symbols(2)

            t1 = threading.Thread(target=worker, args=(0,))
            t2 = threading.Thread(target=worker, args=(1,))
            t1.start(); t2.start()
            t1.join(); t2.join()

            assert results[0] == results[1] == ["A", "B"]
            # single-flight — fetch 1회만
            assert len(fetch_calls) == 1


class TestCacheInfo:
    def test_diagnostic_after_fetch(self):
        async def fake_fetch(n):
            return [f"S{i}" for i in range(n)]

        with patch.object(mod, "_refresh_async", side_effect=fake_fetch):
            mod.get_top_n_symbols(50)
        info = mod.cache_info()
        assert 50 in info
        assert info[50]["size"] == 50
        assert info[50]["age_seconds"] < 5
        assert len(info[50]["sample"]) == 3
