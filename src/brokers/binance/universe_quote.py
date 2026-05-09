"""Binance broker universe-wide daily OHLCV fetcher (#218 Phase 2 P2).

24h 거래량 top-N USDT spot pairs 의 일봉을 일괄 fetch. universe-scan 전략이
매주 리밸 시 사용. 본 모듈은 기존 broker 코드 미변경 — additive only.

Rate-limit:
- Binance public REST weight 한도 1200/min, klines 1 호출 = weight 1
- 30 종목 × 5y daily ~ 30 호출 / 분 (limit 의 2.5%) → 매우 안전
- 24h ticker 일괄 호출 (snapshot universe) weight 40

기존 `scripts/bench_cs_tsmom_crypto.py` 의 fetch 로직과 동일 접근. 라이브 환경
용으로 정형화.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import socket
import time
import urllib.error
import urllib.request

import pandas as pd

log = logging.getLogger(__name__)


def fetch_24h_tickers() -> list[dict]:
    """Binance 전 USDT 스폿 페어의 24h ticker 스냅샷."""
    url = "https://api.binance.com/api/v3/ticker/24hr"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())


def fetch_klines(symbol: str, interval: str = "1d",
                 start_ms: int | None = None, end_ms: int | None = None,
                 limit: int = 1000, retries: int = 3) -> list[list]:
    """단일 심볼 klines 페치, 페이지네이션 안 함 (최대 1000봉). 호출자가 페이지 처리."""
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    if start_ms is not None:
        url += f"&startTime={start_ms}"
    if end_ms is not None:
        url += f"&endTime={end_ms}"
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except (urllib.error.HTTPError, urllib.error.URLError, socket.timeout) as exc:
            last_err = exc
            time.sleep(0.8 + attempt * 0.5)
    raise RuntimeError(f"binance_klines_fetch_fail symbol={symbol}: {last_err}")


def _klines_to_dataframe(rows: list[list]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "tb_base", "tb_quote", "_",
    ])
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype(float)
    df.index = pd.to_datetime(df["open_time"], unit="ms").dt.normalize()
    return df[["open", "high", "low", "close", "volume", "quote_volume"]]


def fetch_universe_klines(
    symbols: list[str],
    *,
    interval: str = "1d",
    start_ms: int | None = None,
    end_ms: int | None = None,
    max_workers: int = 4,
    inter_call_sleep: float = 0.05,
) -> dict[str, pd.DataFrame]:
    """Top-N 심볼의 daily klines 일괄 fetch.

    Args:
        symbols: list of Binance USDT pair symbols (e.g. ["BTCUSDT", "ETHUSDT"]).
        interval: kline interval ("1d", "4h", "1h"). Default daily.
        start_ms / end_ms: epoch milliseconds 시작/끝.
        max_workers: 4 worker (Binance weight 1200/min 한도 충분).
        inter_call_sleep: 워커별 호출 사이 sleep.

    Returns:
        dict[symbol → DataFrame (open, high, low, close, volume, quote_volume)].
        실패 종목 누락.
    """
    panels: dict[str, pd.DataFrame] = {}
    failed: list[str] = []

    def _fetch_one(sym: str):
        try:
            rows = fetch_klines(sym, interval, start_ms, end_ms)
            return sym, _klines_to_dataframe(rows)
        except Exception as exc:
            log.warning("binance_universe_fetch_fail symbol=%s error=%s", sym, exc)
            return sym, None

    started = time.time()
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {}
        for sym in symbols:
            futures[ex.submit(_fetch_one, sym)] = sym
            time.sleep(inter_call_sleep)
        for fut in cf.as_completed(futures):
            sym, df = fut.result()
            if df is None or df.empty:
                failed.append(sym)
            else:
                panels[sym] = df
    elapsed = time.time() - started
    log.info(
        "binance_universe_fetch_complete fetched=%d failed=%d elapsed_s=%.1f",
        len(panels), len(failed), elapsed,
    )
    return panels
