"""Dynamic top-N Binance USDT-perp universe cache.

Used by live-scanner 전략 (예: live-airborne-bb-reversal-kst-hours) 가
``get_universe()`` 에서 daemon (`qta-airborne-daemon`) 과 같은 *동적 top-N*
종목 목록을 받기 위한 공유 캐시.

설계 — sync API + 5분 메모리 TTL:
- ``get_top_n_symbols(n=100)`` 호출 시:
  - 캐시가 fresh (5분 안) → 즉시 반환
  - stale → ``fetch_futures_24h_snapshot`` async 호출 → ``top_n_by_volume`` 로
    필터 → 캐시 update
- async 호출을 sync 진입점에서 처리하려면 ``asyncio.run`` 또는 thread.
  ``LiveScannerMixin.get_universe`` 가 classmethod (sync) 라 sync API 필요.

비상 fallback:
- 첫 호출 실패 (네트워크 오류 등) → 정적 ``BINANCE_USDT_TOP30`` 반환.
  새 universe 받기 실패해도 매매 멈추지 않게 (graceful, never raise).

Thread-safe:
- ``threading.Lock`` 으로 동시 fetch 1회만 (single-flight).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# 5분 TTL — daemon top-N 재산출 주기 (~1h) 보다 훨씬 짧지만, 호출 폭주는 방지.
_CACHE_TTL = timedelta(minutes=5)

# 상태 store (process 전역)
_state_lock = threading.Lock()
_refresh_lock = threading.Lock()
_cache: dict[int, list[str]] = {}
_cache_at: dict[int, datetime] = {}


def _fresh(n: int) -> list[str] | None:
    """캐시가 5분 안 이면 그 list 반환, else None."""
    with _state_lock:
        if n not in _cache or n not in _cache_at:
            return None
        age = datetime.now(timezone.utc) - _cache_at[n]
        if age >= _CACHE_TTL:
            return None
        return list(_cache[n])


def _store(n: int, symbols: list[str]) -> None:
    """캐시 update — atomic via _state_lock."""
    with _state_lock:
        _cache[n] = list(symbols)
        _cache_at[n] = datetime.now(timezone.utc)


def _fallback_universe() -> list[str]:
    """fetch 실패 시 정적 TOP30. 매매 멈추지 않게."""
    from src.portfolio.binance_universe import BINANCE_USDT_TOP30
    return list(BINANCE_USDT_TOP30)


async def _refresh_async(n: int) -> list[str]:
    """async snapshot fetch → top_n_by_volume → list[str]."""
    from src.universe.binance_futures_snapshot import (
        fetch_futures_24h_snapshot,
    )
    from src.universe.binance_top import top_n_by_volume

    snapshot = await fetch_futures_24h_snapshot()
    return list(top_n_by_volume(snapshot, n=n))


def _run_async(coro):
    """async coroutine 을 sync context 에서 실행.

    - 이벤트 루프가 이미 돌고 있으면 (async caller) — 새 thread 에서 run
    - 아니면 직접 ``asyncio.run``
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # 이미 loop 있음 — 별도 thread 에서 실행
    result: list = [None]
    exc: list = [None]

    def _worker():
        try:
            result[0] = asyncio.run(coro)
        except Exception as err:  # noqa: BLE001
            exc[0] = err

    t = threading.Thread(target=_worker)
    t.start()
    t.join(timeout=30)  # 30s 타임아웃 — fapi 호출은 보통 1~2초
    if exc[0] is not None:
        raise exc[0]
    if result[0] is None:
        raise TimeoutError("binance top universe fetch timed out")
    return result[0]


def get_top_n_symbols(n: int = 100) -> list[str]:
    """Dynamic top-N USDT-perp universe. 5분 캐시 + fail-safe fallback.

    Returns:
        list[str] — 24h 거래량 상위 n 종목 (stable/peg/leverage 제외).
        fetch 실패 시 정적 ``BINANCE_USDT_TOP30`` (n 무관) 반환.
    """
    if n <= 0:
        raise ValueError(f"n > 0 required, got {n}")

    fresh = _fresh(n)
    if fresh is not None:
        return fresh

    # single-flight refresh
    with _refresh_lock:
        # 이중 체크 — 다른 thread 가 refresh 끝냈을 수 있음
        fresh = _fresh(n)
        if fresh is not None:
            return fresh
        try:
            symbols = _run_async(_refresh_async(n))
            if not symbols:
                raise RuntimeError("empty top-N response")
            _store(n, symbols)
            logger.info(
                "[binance_top_dynamic] refreshed top-%d (sample: %s)",
                n, symbols[:5],
            )
            return list(symbols)
        except Exception as err:  # noqa: BLE001
            logger.warning(
                "[binance_top_dynamic] fetch failed (%s) — falling back to "
                "static BINANCE_USDT_TOP30", err,
            )
            return _fallback_universe()


def clear_cache() -> None:
    """Tests / manual override 용 — 캐시 비움."""
    with _state_lock:
        _cache.clear()
        _cache_at.clear()


def cache_info() -> dict:
    """diagnostic — 어떤 n 의 캐시가 언제 갱신됐는지."""
    with _state_lock:
        return {
            n: {
                "size": len(symbols),
                "age_seconds": (
                    datetime.now(timezone.utc) - _cache_at[n]
                ).total_seconds() if n in _cache_at else None,
                "sample": symbols[:3],
            }
            for n, symbols in _cache.items()
        }
