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


_cache: dict[int, list[str]] = {}
_cache_at: dict[int, float] = {}
_state_lock = threading.Lock()
_refresh_lock = threading.Lock()


def _fresh(n: int) -> list[str] | None:
    with _state_lock:
        ts = _cache_at.get(n)
        if ts is None:
            return None
        if (time.time() - ts) >= _TTL_SEC:
            return None
        return list(_cache.get(n, []))


def _store(n: int, symbols: list[str]) -> None:
    with _state_lock:
        _cache[n] = list(symbols)
        _cache_at[n] = time.time()


def _fallback_universe() -> list[str]:
    return [s for s in BITGET_USDT_TOP30 if not _is_excluded(s)]


def _fetch_tickers_sync(n: int) -> list[str]:
    """Bitget tickers REST → top-n by usdtVolume."""
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
        out.append(sym)
        if len(out) >= n:
            break
    return out


def get_top_n_symbols(n: int = 100) -> list[str]:
    """Dynamic top-N USDT-perp universe (24h volume). 5분 캐시 + fallback.

    Returns:
        list[str] — Bitget 거래량 상위 n 종목. fetch 실패 시 정적
        ``BITGET_USDT_TOP30`` (n 무관).
    """
    if n <= 0:
        raise ValueError(f"n > 0 required, got {n}")

    fresh = _fresh(n)
    if fresh is not None:
        return fresh

    with _refresh_lock:
        fresh = _fresh(n)
        if fresh is not None:
            return fresh
        try:
            symbols = _fetch_tickers_sync(n)
            if not symbols:
                raise RuntimeError("empty top-N response")
            _store(n, symbols)
            logger.info(
                "[bitget_top_dynamic] refreshed top-%d (sample: %s)",
                n, symbols[:5],
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
