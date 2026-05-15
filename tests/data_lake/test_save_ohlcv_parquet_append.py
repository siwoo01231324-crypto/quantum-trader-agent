"""S7 (#231) — save_ohlcv_parquet append + dedup behavior.

Regression target: pre-fix cron_fetch_kis_daily 가 매일 같은 partition path
(year/month/symbol/part-0.parquet) 에 새 데이터로 overwrite → 5/11~5/13 적재
데이터 5/14 fetch 로 손실. 본 patch 는 read existing → concat → dedup → write.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from src.data_lake.fetcher import save_ohlcv_parquet


def _make_bar(symbol: str, ts: str, close: float) -> dict:
    """Single OHLCV row matching OHLCV_SCHEMA."""
    return {
        "symbol": symbol,
        "ts": pd.Timestamp(ts, tz="UTC"),
        "freq": "1m",
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1000.0, "vwap": close, "trade_count": 5,
        "source": "kis_paper",
        "ingested_at": pd.Timestamp.now(tz="UTC"),
    }


def test_first_write_creates_partition(tmp_path):
    """No existing parquet → standard write (regression: 같은 단일 write 동작)."""
    df = pd.DataFrame([_make_bar("005930", "2026-05-14T00:00:00+00:00", 70000.0)])
    paths = save_ohlcv_parquet(df, tmp_path, "005930", "1m")
    assert len(paths) == 1
    assert paths[0].exists()
    loaded = pq.ParquetFile(paths[0]).read().to_pandas()
    assert len(loaded) == 1


def test_second_write_appends_not_overwrites(tmp_path):
    """Same symbol/partition, different ts → 2 rows after second write."""
    df1 = pd.DataFrame([_make_bar("005930", "2026-05-14T01:00:00+00:00", 70000.0)])
    save_ohlcv_parquet(df1, tmp_path, "005930", "1m")

    df2 = pd.DataFrame([_make_bar("005930", "2026-05-14T01:01:00+00:00", 70100.0)])
    paths = save_ohlcv_parquet(df2, tmp_path, "005930", "1m")

    loaded = pq.ParquetFile(paths[0]).read().to_pandas()
    assert len(loaded) == 2, "second write should append, not overwrite"
    assert set(loaded["ts"].dt.minute) == {0, 1}


def test_duplicate_ts_dedup_keeps_last(tmp_path):
    """Same (symbol, ts) re-fetched → 1 row only (dedup keeps last write)."""
    # First fetch — close=70000
    df1 = pd.DataFrame([_make_bar("005930", "2026-05-14T01:00:00+00:00", 70000.0)])
    save_ohlcv_parquet(df1, tmp_path, "005930", "1m")

    # Same ts re-fetched (e.g. minute boundary refresh) — close=70050 (more accurate)
    df2 = pd.DataFrame([_make_bar("005930", "2026-05-14T01:00:00+00:00", 70050.0)])
    paths = save_ohlcv_parquet(df2, tmp_path, "005930", "1m")

    loaded = pq.ParquetFile(paths[0]).read().to_pandas()
    assert len(loaded) == 1, "duplicate ts must be deduped"
    assert loaded["close"].iloc[0] == 70050.0, "keep='last' wins"


def test_multi_day_accumulation_no_loss(tmp_path):
    """Simulate the original bug: 3-day fetch should accumulate, not lose 2 days."""
    for day in range(11, 14):
        df = pd.DataFrame([
            _make_bar("005930", f"2026-05-{day:02d}T01:00:00+00:00", 70000.0 + day),
        ])
        paths = save_ohlcv_parquet(df, tmp_path, "005930", "1m")

    # All 3 days share the same (year=2026, month=05) partition.
    loaded = pq.ParquetFile(paths[0]).read().to_pandas()
    assert len(loaded) == 3, "all 3 days should remain in lake"
    days_present = sorted(loaded["ts"].dt.day.tolist())
    assert days_present == [11, 12, 13]
