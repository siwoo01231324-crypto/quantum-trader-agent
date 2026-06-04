"""Sync OHLCV fetcher for Bitget USDT-M Futures universes.

Mirrors ``src/brokers/binance/universe_quote.py`` API. Returns a
``dict[symbol, pd.DataFrame]`` with index = bar open time (UTC) and columns
``open/high/low/close/volume`` — same shape SnapshotBuilder expects.

Used by ``scripts/live_run._build_universe_quote_provider`` when
``broker_mode in {bitget-demo, bitget-mainnet}``.
"""
from __future__ import annotations

import logging
from typing import Iterable

import httpx
import pandas as pd

from src.brokers.bitget.async_http import REST_BASE_LIVE
from src.brokers.bitget.market_ws import _CANDLE_INTERVAL_CHANNEL  # interval validation

log = logging.getLogger(__name__)

_DEFAULT_LIMIT = 200
_TIMEOUT = 15.0

# 2026-06-05 — Bitget 미상장 종목 (Binance top-100 의 AIA/BONK/GUA/HEI/NOK/PHAROS/...)
# 사전 필터링. 미상장 종목 호출은 매 ~5분 universe refresh 마다 WARNING 폭주 +
# REST limit 낭비. ``/contracts`` 1회 호출로 받은 set 을 1h 캐시.
_CONTRACT_SET: set[str] | None = None
_CONTRACT_FETCHED_AT: float = 0.0
_CONTRACT_TTL_SEC: float = 3600.0


def _load_bitget_contract_set(*, base_url: str) -> set[str]:
    """Return cached set of supported Bitget USDT-FUTURES symbols (1h TTL)."""
    global _CONTRACT_SET, _CONTRACT_FETCHED_AT
    import time as _time
    now = _time.time()
    if _CONTRACT_SET is not None and (now - _CONTRACT_FETCHED_AT) < _CONTRACT_TTL_SEC:
        return _CONTRACT_SET
    try:
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.get(
                f"{base_url}/api/v2/mix/market/contracts",
                params={"productType": "USDT-FUTURES"},
            )
        if r.status_code == 200:
            j = r.json()
            if str(j.get("code")) == "00000":
                _CONTRACT_SET = {c["symbol"] for c in (j.get("data") or [])}
                _CONTRACT_FETCHED_AT = now
                log.info("bitget contracts list refreshed: %d symbols", len(_CONTRACT_SET))
                return _CONTRACT_SET
        log.warning("bitget contracts fetch failed status=%d — skip filter",
                    r.status_code)
    except Exception as exc:  # noqa: BLE001
        log.warning("bitget contracts fetch exception (%s) — skip filter", exc)
    # On failure, return empty set → filter skipped (legacy behaviour).
    return set()


def _interval_to_granularity(interval: str) -> str:
    ch = _CANDLE_INTERVAL_CHANNEL.get(interval)
    if ch is None:
        raise ValueError(
            f"unsupported interval '{interval}'; expected one of {sorted(_CANDLE_INTERVAL_CHANNEL)}"
        )
    # candle channel suffix == granularity (1m / 5m / 1H / 1D / ...).
    return ch.removeprefix("candle")


def _bars_to_df(rows: list) -> pd.DataFrame:
    """Bitget candle row: [ts_ms, open, high, low, close, baseVol, quoteVol]."""
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "_quote_vol"])
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    df = df.set_index("ts")[["open", "high", "low", "close", "volume"]]
    for c in df.columns:
        df[c] = df[c].astype(float)
    return df.sort_index()


def fetch_universe_klines(
    symbols: Iterable[str],
    interval: str = "1h",
    *,
    limit: int = _DEFAULT_LIMIT,
    base_url: str = REST_BASE_LIVE,
) -> dict[str, pd.DataFrame]:
    """Fetch klines for ``symbols`` and return {symbol: DataFrame}.

    Failed symbols are silently omitted (matches Binance behaviour). Used at
    snapshot-build time so a single broken symbol must NOT fail the snapshot.
    """
    granularity = _interval_to_granularity(interval)
    out: dict[str, pd.DataFrame] = {}
    syms = list(symbols)
    if not syms:
        return out

    # Pre-filter against Bitget supported symbols → skip silent (status=400 폭주 차단).
    supported = _load_bitget_contract_set(base_url=base_url)
    if supported:
        filtered = [s for s in syms if s in supported]
        skipped = len(syms) - len(filtered)
        if skipped > 0:
            log.info("bitget candles pre-filter: %d/%d symbols (%d unsupported)",
                     len(filtered), len(syms), skipped)
        syms = filtered

    with httpx.Client(timeout=_TIMEOUT) as c:
        for sym in syms:
            try:
                r = c.get(
                    f"{base_url}/api/v2/mix/market/candles",
                    params={
                        "symbol": sym,
                        "productType": "USDT-FUTURES",
                        "granularity": granularity,
                        "limit": str(limit),
                    },
                )
                if r.status_code != 200:
                    log.warning("bitget candles %s status=%d", sym, r.status_code)
                    continue
                j = r.json()
                if str(j.get("code")) != "00000":
                    log.warning("bitget candles %s code=%s msg=%s",
                                sym, j.get("code"), j.get("msg"))
                    continue
                out[sym] = _bars_to_df(j.get("data") or [])
            except Exception as exc:  # noqa: BLE001
                log.warning("bitget candles %s exc=%s", sym, exc)
                continue
    return out
