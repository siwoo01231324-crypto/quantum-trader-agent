"""KIS broker universe-wide daily OHLCV fetcher (#218 Phase 2 P1).

기존 `src/brokers/kis/price_client.py` 의 단건 `fetch_daily_ohlcv_raw` 를 thread-pool
+ throttle wrapping 하여 universe-scan 전략이 매주 리밸 시 top-N 종목 일봉을
일괄 fetch 할 수 있게 한다.

본 모듈은 기존 broker 코드 미변경 — additive only. 호출자가 명시적으로 import
한 경우에만 동작하며, 기존 단일종목 path 영향 0.

Rate-limit 정책 (#212/#213 정합):
- KIS paper 한도 2 req/s, live 한도 다름 (계좌별)
- _RATE_LIMIT_SLEEP=0.5s 사이 호출 (paper 안전 마진)
- 429 발생 시 fetch_daily_ohlcv_raw 의 _call_with_429_retry 가 backoff
- worker 수: paper=2, live=1 권장 (rate-limit 위험 회피)

매주 리밸 시 350 종목 fetch:
- worker=2, sleep=0.5s → 350 / (2/0.5) = ~88s
- 운영상 금요일 마감 (15:30 KST) 직후 spike 허용 시간 충분
"""
from __future__ import annotations

import concurrent.futures as cf
import logging
import time
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from src.brokers.kis.rest import KISClient

log = logging.getLogger(__name__)


def _bars_to_dataframe(bars) -> pd.DataFrame:
    """KISDailyBar list → OHLCV DataFrame (chronological)."""
    if not bars:
        return pd.DataFrame()
    rows = []
    for b in bars:
        rows.append({
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": float(b.volume),
        })
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime([b.date for b in bars])
    df.index.name = "date"
    return df


def fetch_universe_snapshot(
    client: "KISClient",
    symbols: list[str],
    start: str,
    end: str,
    *,
    period: str = "D",
    max_workers: int = 2,
    inter_call_sleep: float = 0.5,
) -> dict[str, pd.DataFrame]:
    """Fetch daily OHLCV for a list of KRX symbols, returning a panel dict.

    Args:
        client: configured KISClient (auth + rate_limiter wired).
        symbols: list of 6-digit KRX codes.
        start, end: "YYYYMMDD" strings.
        period: "D" / "W" / "M". Default daily.
        max_workers: concurrent thread count. Paper 2 권장 (rate-limit 안전).
        inter_call_sleep: 워커별 sleep 사이 (rate-limit 보강). KIS paper 2 rps
                         한도 안에 들도록 0.5s 권장.

    Returns:
        dict[symbol → DataFrame (open, high, low, close, volume)]. 실패 종목은
        결과에서 누락 (universe-scan 전략은 누락 종목을 ineligible 처리 가능).
    """
    from src.brokers.kis.price_client import fetch_daily_ohlcv_raw

    panels: dict[str, pd.DataFrame] = {}
    failed: list[str] = []

    def _fetch_one(sym: str) -> tuple[str, pd.DataFrame | None]:
        try:
            bars = fetch_daily_ohlcv_raw(client, sym, start, end, period)
            return sym, _bars_to_dataframe(bars)
        except Exception as exc:
            log.warning("kis_universe_fetch_fail symbol=%s error=%s", sym, exc)
            return sym, None

    started = time.time()
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {}
        for sym in symbols:
            futures[ex.submit(_fetch_one, sym)] = sym
            time.sleep(inter_call_sleep / max_workers)
        for fut in cf.as_completed(futures):
            sym, df = fut.result()
            if df is None or df.empty:
                failed.append(sym)
            else:
                panels[sym] = df
    elapsed = time.time() - started
    log.info(
        "kis_universe_fetch_complete fetched=%d failed=%d elapsed_s=%.1f",
        len(panels), len(failed), elapsed,
    )
    return panels
