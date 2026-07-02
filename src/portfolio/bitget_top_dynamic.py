"""Bitget USDT-M Futures 24h-volume top-N universe (mirrors binance_top_dynamic).

Endpoint: ``GET /api/v2/mix/market/tickers?productType=USDT-FUTURES`` —
공개 API, 인증 불필요. ~597 종 응답. ``usdtVolume`` (24h quote-coin 거래대금)
desc 정렬 후 stable/peg/leverage 토큰 제외 → top n.

API surface (caller compatibility):
    get_top_n_symbols(n=100) -> list[str]
    clear_cache() -> None
    cache_info() -> dict

5분 캐시 + 단일-flight. fetch 실패 시 정적 BITGET_USDT_TOP30 fallback —
strategy.get_universe() 가 절대 빈 list 안 받음 (graceful, 매매 정지 X).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TTL_SEC = 300.0
_TIMEOUT = 10.0
_BITGET_BASE = "https://api.bitget.com"

# 정적 fallback — fetch 실패 시. 사용자 universe 13/15 호환 검증된 종목 +
# 주요 거래 30종 baseline.
BITGET_USDT_TOP30: list[str] = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT",
    "ADAUSDT", "BNBUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "LTCUSDT", "ATOMUSDT", "BCHUSDT", "NEARUSDT", "ARBUSDT",
    "AAVEUSDT", "OPUSDT", "INJUSDT", "FETUSDT", "APTUSDT",
    "PEPEUSDT", "SHIBUSDT", "WLDUSDT", "TIAUSDT", "SUIUSDT",
    "TRUMPUSDT", "FARTCOINUSDT", "VIRTUALUSDT", "AXSUSDT", "XLMUSDT",
]

# 제외 패턴 (stable/peg/leverage 토큰).
_EXCLUDE_PREFIXES = ("USDC", "BUSD", "USDT_", "FDUSD")
_EXCLUDE_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

# 종목 단위 제외 — 봇이 절대 진입하면 안 되는 심볼.
# 2026-06-22: ORDIUSDT 는 사용자가 수동으로 보유·관리하는 포지션. 같은 Bitget
# 계좌(one-way/cross)에서 봇이 진입하면 네팅 충돌로 수동 물량이 차감/뒤집힘 →
# 진입 자체를 차단(라이브 두 게이트 모두 get_universe→본 함수 경유). env
# ``BITGET_UNIVERSE_EXCLUDE`` (콤마구분) 로 런타임 추가 가능.
_EXCLUDE_SYMBOLS: frozenset[str] = frozenset(
    {"ORDIUSDT"}
    | {
        s.strip().upper()
        for s in os.environ.get("BITGET_UNIVERSE_EXCLUDE", "").split(",")
        if s.strip()
    }
)


def _is_excluded(symbol: str) -> bool:
    if symbol in _EXCLUDE_SYMBOLS:
        return True
    if any(symbol.startswith(p) for p in _EXCLUDE_PREFIXES):
        return True
    if any(symbol.endswith(s) for s in _EXCLUDE_SUFFIXES):
        return True
    return False


_cache: dict[tuple[int, bool], list[str]] = {}
_cache_at: dict[tuple[int, bool], float] = {}
_state_lock = threading.Lock()
_refresh_lock = threading.Lock()

# RWA(토큰화주식·금속) 심볼 집합 캐시 — contracts.isRwa 기반 (2026-07-02).
_rwa_cache: set[str] | None = None
_rwa_cache_at: float = float("-inf")


def _fresh(key: tuple[int, bool]) -> list[str] | None:
    with _state_lock:
        ts = _cache_at.get(key)
        if ts is None:
            return None
        if (time.time() - ts) >= _TTL_SEC:
            return None
        return list(_cache.get(key, []))


def _store(key: tuple[int, bool], symbols: list[str]) -> None:
    with _state_lock:
        _cache[key] = list(symbols)
        _cache_at[key] = time.time()


def _fetch_rwa_set() -> frozenset[str]:
    """Bitget contracts 에서 ``isRwa=YES`` (토큰화주식·금속·RWA) 심볼 집합 — 5분 캐시.

    SOXL·MSTR·NVDA·XAU·XAG 등 실물자산 연동 토큰. 크립토(BTC·HYPE·PEPE)는 NO.
    fetch 실패 시 빈 set(RWA 필터 미적용 = 안전하게 전부 통과)."""
    global _rwa_cache, _rwa_cache_at
    with _state_lock:
        if _rwa_cache is not None and (time.time() - _rwa_cache_at) < _TTL_SEC:
            return frozenset(_rwa_cache)
    try:
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.get(
                f"{_BITGET_BASE}/api/v2/mix/market/contracts",
                params={"productType": "USDT-FUTURES"},
            )
        r.raise_for_status()
        rows = (r.json() or {}).get("data") or []
        rwa = frozenset(
            str(row.get("symbol", "")) for row in rows
            if str(row.get("isRwa", "")).strip().lower() in ("yes", "true", "1")
        )
        with _state_lock:
            _rwa_cache = set(rwa)
            _rwa_cache_at = time.time()
        return rwa
    except Exception as err:  # noqa: BLE001 — RWA 필터 실패가 매매 막지 않음
        logger.warning(
            "[bitget_top_dynamic] RWA(isRwa) fetch 실패 (%s) — RWA 필터 미적용", err,
        )
        return frozenset()


def _fallback_universe() -> list[str]:
    return [s for s in BITGET_USDT_TOP30 if not _is_excluded(s)]


def _fetch_tickers_sync(n: int, exclude_rwa: bool = False) -> list[str]:
    """Bitget tickers REST → top-n by usdtVolume. exclude_rwa 면 RWA(주식·금속) 제외."""
    with httpx.Client(timeout=_TIMEOUT) as c:
        r = c.get(
            f"{_BITGET_BASE}/api/v2/mix/market/tickers",
            params={"productType": "USDT-FUTURES"},
        )
    r.raise_for_status()
    j = r.json()
    if str(j.get("code")) != "00000":
        raise RuntimeError(f"bitget tickers code={j.get('code')} msg={j.get('msg')}")
    rows = j.get("data") or []
    rwa = _fetch_rwa_set() if exclude_rwa else frozenset()
    # usdtVolume desc 정렬 + exclude filter.
    def _vol(row: dict) -> float:
        try:
            return float(row.get("usdtVolume") or row.get("quoteVolume") or 0)
        except (ValueError, TypeError):
            return 0.0
    sorted_rows = sorted(rows, key=_vol, reverse=True)
    out: list[str] = []
    for row in sorted_rows:
        sym = str(row.get("symbol", ""))
        if not sym or _is_excluded(sym):
            continue
        if sym in rwa:  # 토큰화주식·금속 (isRwa=YES) 제외 — 크립토만
            continue
        out.append(sym)
        if len(out) >= n:
            break
    return out


def get_top_n_symbols(n: int = 100, *, exclude_rwa: bool = False) -> list[str]:
    """Dynamic top-N USDT-perp universe (24h volume). 5분 캐시 + fallback.

    Args:
        n: 상위 종목 수.
        exclude_rwa: True 면 RWA(토큰화주식·금속, ``isRwa=YES``) 제외 → 크립토만.
            macross 등 크립토 검증 전략용. 기본 False = 기존 동작(전부 포함) 보존.

    Returns:
        list[str] — Bitget 거래량 상위 n 종목. fetch 실패 시 정적
        ``BITGET_USDT_TOP30`` (n 무관).
    """
    if n <= 0:
        raise ValueError(f"n > 0 required, got {n}")

    key = (n, bool(exclude_rwa))
    fresh = _fresh(key)
    if fresh is not None:
        return fresh

    with _refresh_lock:
        fresh = _fresh(key)
        if fresh is not None:
            return fresh
        try:
            symbols = _fetch_tickers_sync(n, exclude_rwa=exclude_rwa)
            if not symbols:
                raise RuntimeError("empty top-N response")
            _store(key, symbols)
            logger.info(
                "[bitget_top_dynamic] refreshed top-%d%s (sample: %s)",
                n, " (crypto-only)" if exclude_rwa else "", symbols[:5],
            )
            return list(symbols)
        except Exception as err:  # noqa: BLE001
            logger.warning(
                "[bitget_top_dynamic] fetch failed (%s) — falling back to "
                "static BITGET_USDT_TOP30", err,
            )
            return _fallback_universe()


def clear_cache() -> None:
    with _state_lock:
        _cache.clear()
        _cache_at.clear()


def cache_info() -> dict[str, Any]:
    with _state_lock:
        return {
            "entries": list(_cache.keys()),
            "ages_sec": {n: time.time() - ts for n, ts in _cache_at.items()},
        }
