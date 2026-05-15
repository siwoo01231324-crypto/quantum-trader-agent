"""Binance REST API → Parquet data fetcher.

Fetches historical OHLCV candle data from Binance klines endpoint with
pagination (1000 candles/request), rate-limit retry, and hive-partitioned
Parquet output.

Also provides fetch_kis_daily_ohlcv for KRX/KIS daily bars and
fetch_kis_intraday_ohlcv for KRX/KIS intraday minute bars.
"""
from __future__ import annotations

import io
import logging
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

from data_lake import OHLCV_SCHEMA, validate_schema, partition_path
from src.brokers.kis.rest import KISClient
from src.brokers.kis.price_client import fetch_daily_ohlcv_raw, fetch_intraday_ohlcv_raw
from src.universe.krx_calendar import is_krx_holiday, KST

if TYPE_CHECKING:
    from src.brokers.kis.auth import KISAuth

log = logging.getLogger(__name__)

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_VISION_BASE = "https://data.binance.vision/data/spot/monthly/klines"

_LIMIT = 1000  # max candles per request
_SLEEP_BETWEEN = 0.5  # seconds between paginated requests
_MAX_RETRIES = 3  # max retries on 429
_RETRY_BASE = 1.0  # base seconds for exponential backoff


def _parse_klines(
    raw: list,
    *,
    symbol: str,
    interval: str,
    now: datetime,
    source_label: str = "binance",
) -> list[dict]:
    """Convert raw Binance kline rows to OHLCV_SCHEMA dicts.

    ``source_label`` distinguishes Spot ("binance") vs Futures USDT-M
    ("binance_futures") in the lake schema.
    """
    records = []
    for row in raw:
        open_time_ms = int(row[0])
        ts = datetime.fromtimestamp(open_time_ms / 1000.0, tz=timezone.utc)
        volume = float(row[5])
        quote_vol = float(row[7])
        vwap = (quote_vol / volume) if volume != 0.0 else 0.0
        records.append({
            "symbol": symbol,
            "ts": pd.Timestamp(ts),
            "freq": interval,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": volume,
            "vwap": vwap,
            "trade_count": int(row[8]),
            "source": source_label,
            "ingested_at": pd.Timestamp(now),
        })
    return records


def _get_with_retry(url: str, params: dict) -> list:
    """GET request with exponential backoff on 429. Returns parsed JSON list."""
    delay = _RETRY_BASE
    for attempt in range(_MAX_RETRIES + 1):
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            if attempt < _MAX_RETRIES:
                time.sleep(delay)
                delay *= 2
                continue
            resp.raise_for_status()
        resp.raise_for_status()
    return []  # unreachable


def _paginate_binance_klines(
    *,
    base_url: str,
    symbol: str,
    interval: str,
    start: str,
    end: str,
    source_label: str,
) -> pd.DataFrame:
    """Shared pagination loop for Binance klines (Spot + Futures USDT-M).

    Same wire protocol on both endpoints (same row schema, same query params,
    same 1000-row page limit). Only ``base_url`` and ``source_label`` differ.
    """
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    now = datetime.now(tz=timezone.utc)
    all_records: list[dict] = []
    current_start_ms = start_ms

    while True:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start_ms,
            "endTime": end_ms,
            "limit": _LIMIT,
        }
        raw = _get_with_retry(base_url, params)
        if not raw:
            break

        records = _parse_klines(
            raw, symbol=symbol, interval=interval, now=now, source_label=source_label,
        )
        all_records.extend(records)

        if len(raw) < _LIMIT:
            # Last page
            break

        # Advance startTime to one ms after the last candle's open time
        last_open_ms = int(raw[-1][0])
        current_start_ms = last_open_ms + 1

        if current_start_ms >= end_ms:
            break

        time.sleep(_SLEEP_BETWEEN)

    if not all_records:
        return pd.DataFrame(columns=list(OHLCV_SCHEMA.keys()))

    return pd.DataFrame(all_records)


def fetch_binance_klines(
    symbol: str,
    interval: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    """Fetch Spot candles from Binance REST API with pagination and rate limiting.

    Parameters
    ----------
    symbol:   e.g. "BTCUSDT"
    interval: e.g. "15m", "1h"
    start:    ISO date string e.g. "2025-04-01"
    end:      ISO date string e.g. "2026-04-01"

    Returns
    -------
    pd.DataFrame with columns matching OHLCV_SCHEMA. ``source="binance"``.
    """
    return _paginate_binance_klines(
        base_url=BINANCE_KLINES_URL,
        symbol=symbol,
        interval=interval,
        start=start,
        end=end,
        source_label="binance",
    )


def fetch_binance_futures_klines(
    symbol: str,
    interval: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    """Fetch Binance USDT-M Futures candles (public ``fapi/v1/klines``).

    Same wire schema as Spot — only the endpoint and the ``source`` label differ.
    Used by #80 Phase E ``shadow_report.py --compare-backtest`` (data_source =
    ``"binance_futures_usdtm"``) for Sharpe parity validation against
    Phase 1 Shadow Paper runs.

    Parameters
    ----------
    symbol:   e.g. "BTCUSDT", "ETHUSDT", "SOLUSDT"
    interval: e.g. "1m", "15m", "1h"
    start:    ISO date string e.g. "2026-04-01"
    end:      ISO date string e.g. "2026-04-26"

    Returns
    -------
    pd.DataFrame with columns matching OHLCV_SCHEMA. ``source="binance_futures"``.
    """
    return _paginate_binance_klines(
        base_url=BINANCE_FUTURES_KLINES_URL,
        symbol=symbol,
        interval=interval,
        start=start,
        end=end,
        source_label="binance_futures",
    )


def fetch_binance_vision_klines(
    symbol: str,
    interval: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    """Fetch klines from Binance Vision public S3 dump (no geo-block, no rate limits).

    Binance Vision publishes monthly OHLCV zips at:
        https://data.binance.vision/data/spot/monthly/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{YYYY}-{MM}.zip

    Each zip contains a single CSV with columns matching the REST API response order.
    Use this in CI environments where api.binance.com returns 451 (e.g. GitHub-hosted runners).

    The current month may not be published yet — partial data is silently skipped.
    """
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    now = datetime.now(tz=timezone.utc)

    months: list[tuple[int, int]] = []
    cur = datetime(start_dt.year, start_dt.month, 1, tzinfo=timezone.utc)
    while cur <= end_dt:
        months.append((cur.year, cur.month))
        cur = datetime(cur.year + 1, 1, 1, tzinfo=timezone.utc) if cur.month == 12 \
            else datetime(cur.year, cur.month + 1, 1, tzinfo=timezone.utc)

    all_records: list[dict] = []
    for year, month in months:
        fname = f"{symbol}-{interval}-{year:04d}-{month:02d}"
        url = f"{BINANCE_VISION_BASE}/{symbol}/{interval}/{fname}.zip"
        resp = requests.get(url, timeout=60)
        if resp.status_code == 404:
            # Month not yet published (typically the current month) — skip.
            continue
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            with zf.open(f"{fname}.csv") as f:
                df = pd.read_csv(f, header=None, names=[
                    "open_time", "open", "high", "low", "close", "volume",
                    "close_time", "quote_asset_volume", "number_of_trades",
                    "taker_buy_base_volume", "taker_buy_quote_volume", "ignore",
                ])
        # Newer Binance Vision dumps (post-2025) use microsecond timestamps;
        # older dumps use milliseconds. Normalize to ms before _parse_klines.
        # ms for year 2030 ≈ 1.9e12; μs for year 2025 ≈ 1.7e15.
        if not df.empty and df["open_time"].iloc[0] > 1e14:
            df["open_time"] = df["open_time"] // 1000
            df["close_time"] = df["close_time"] // 1000

        # Convert each row to OHLCV_SCHEMA dict via _parse_klines (expects list-of-list).
        raw = df[[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_volume", "taker_buy_quote_volume", "ignore",
        ]].values.tolist()
        records = _parse_klines(raw, symbol=symbol, interval=interval, now=now)
        # Filter to [start, end] window since we pulled whole months.
        for rec in records:
            if start_dt <= rec["ts"].to_pydatetime() <= end_dt:
                all_records.append(rec)

    if not all_records:
        return pd.DataFrame(columns=list(OHLCV_SCHEMA.keys()))
    return pd.DataFrame(all_records)


def save_ohlcv_parquet(
    df: pd.DataFrame,
    output_dir: Path,
    symbol: str,
    freq: str,
) -> list[Path]:
    """Save OHLCV DataFrame to Parquet with hive partitioning by year/month.

    Path pattern:
        output_dir/ohlcv/freq={freq}/year=YYYY/month=MM/symbol={symbol}/part-0.parquet

    Parameters
    ----------
    df:         DataFrame with OHLCV_SCHEMA columns.
    output_dir: Root directory for the data lake.
    symbol:     e.g. "BTCUSDT"
    freq:       e.g. "15m"

    Returns
    -------
    List of Path objects for each written parquet file.
    """
    # Validate schema (key presence only)
    if len(df) > 0:
        sample = df.iloc[0].to_dict()
        errors = validate_schema("ohlcv", sample)
        if errors:
            raise ValueError(f"OHLCV schema validation failed: {errors}")

    output_dir = Path(output_dir)
    written: list[Path] = []

    # Group by year and month
    ts_col = pd.to_datetime(df["ts"], utc=True)
    groups = df.groupby([ts_col.dt.year, ts_col.dt.month])

    for (year, month), group_df in groups:
        rel = partition_path(
            "ohlcv",
            symbol=symbol,
            ts_year=int(year),
            ts_month=int(month),
            freq=freq,
        )
        part_dir = output_dir / rel
        part_dir.mkdir(parents=True, exist_ok=True)
        out_path = part_dir / "part-0.parquet"

        # #231 S7 — append + dedup. 기존 cron 패턴 (매일 같은 partition 에
        # 새 데이터로 overwrite) → 데이터 손실. 누적 패턴 변경:
        # 1) 기존 파일이 있으면 read → concat 후 (symbol, ts) dedup
        # 2) merge 결과 ts 정렬 + 새 part-0.parquet 로 write
        # 손상된 기존 파일 → overwrite (warn log) — fail-safe.
        merged_df = group_df.reset_index(drop=True)
        if out_path.exists():
            try:
                # ParquetFile (single file) — ParquetDataset 의 directory
                # partition discovery 회피 (symbol/freq/year/month 를 directory
                # 명으로 dictionary-cast 하다가 schema 충돌).
                existing = pq.ParquetFile(out_path).read().to_pandas()
                # pyarrow dictionary-encoded string columns 를 plain str 로 cast —
                # 그렇지 않으면 concat 결과를 다시 write 할 때 schema 불일치 발생.
                for col in ("symbol", "freq", "source"):
                    if col in existing.columns:
                        existing[col] = existing[col].astype(str)
                combined = pd.concat([existing, merged_df], ignore_index=True)
                combined = combined.drop_duplicates(
                    subset=["symbol", "ts"], keep="last",
                )
                combined = combined.sort_values("ts").reset_index(drop=True)
                merged_df = combined
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "save_ohlcv_parquet: existing %s read failed (%s) — "
                    "overwriting (data loss possible)", out_path, exc,
                )

        table = pa.Table.from_pandas(merged_df)
        pq.write_table(table, out_path)
        written.append(out_path)

    return written


def fetch_kis_daily_ohlcv(
    symbol: str,
    start: str,
    end: str,
    *,
    auth: "KISAuth",
    app_key: str,
    app_secret: str,
    cano: str,
    acnt_prdt_cd: str,
    paper: bool = True,
) -> pd.DataFrame:
    """Fetch KIS daily OHLCV bars and return a DataFrame matching OHLCV_SCHEMA.

    Parameters
    ----------
    symbol:        KRX stock code (6 digits), e.g. "005930".
    start:         ISO date string "YYYY-MM-DD".
    end:           ISO date string "YYYY-MM-DD".
    auth:          KISAuth instance.
    app_key/app_secret/cano/acnt_prdt_cd: KIS API credentials.
    paper:         True = paper (openapivts), False = live.

    Returns
    -------
    pd.DataFrame with columns matching OHLCV_SCHEMA. Empty DataFrame (with
    correct columns) if no data is available.
    """
    # Convert ISO dates to YYYYMMDD for KIS API
    start_yyyymmdd = start.replace("-", "")
    end_yyyymmdd = end.replace("-", "")

    client = KISClient(
        auth=auth,
        app_key=app_key,
        app_secret=app_secret,
        cano=cano,
        acnt_prdt_cd=acnt_prdt_cd,
        paper=paper,
    )

    bars = fetch_daily_ohlcv_raw(client, symbol, start_yyyymmdd, end_yyyymmdd)

    if not bars:
        return pd.DataFrame(columns=list(OHLCV_SCHEMA.keys()))

    now = datetime.now(tz=timezone.utc)
    records = []
    for bar in bars:
        # Parse YYYYMMDD date as midnight KST (UTC+9) → UTC
        try:
            ts = pd.Timestamp(
                f"{bar.date[:4]}-{bar.date[4:6]}-{bar.date[6:8]} 15:30:00",
                tz="Asia/Seoul",
            ).tz_convert("UTC")
        except Exception:
            ts = pd.Timestamp(bar.date, tz="UTC")

        records.append({
            "symbol": symbol,
            "ts": ts,
            "freq": "1d",
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": float(bar.volume),
            "vwap": float(bar.trade_amt) / float(bar.volume) if bar.volume != 0.0 else 0.0,
            "trade_count": 0,
            "source": "kis",
            "ingested_at": pd.Timestamp(now),
        })

    return pd.DataFrame(records)


def fetch_kis_intraday_ohlcv(
    symbol: str,
    start: str,
    end: str,
    interval: str = "15",
    *,
    auth: "KISAuth",
    app_key: str,
    app_secret: str,
    cano: str,
    acnt_prdt_cd: str,
    paper: bool = True,
) -> pd.DataFrame:
    """Fetch KIS intraday minute OHLCV bars and return a DataFrame matching OHLCV_SCHEMA.

    Parameters
    ----------
    symbol:        KRX stock code (6 digits), e.g. "005930".
    start:         ISO date string "YYYY-MM-DD".
    end:           ISO date string "YYYY-MM-DD".
    interval:      Bar interval in minutes: "1"|"3"|"5"|"10"|"15"|"30"|"60". Default "15".
    auth:          KISAuth instance.
    app_key/app_secret/cano/acnt_prdt_cd: KIS API credentials.
    paper:         True = paper (openapivts), False = live.

    Returns
    -------
    pd.DataFrame with columns matching OHLCV_SCHEMA. Empty DataFrame (with
    correct columns) if no data is available.

    Notes
    -----
    KIS intraday API only supports data within the last 30 days. Dates older
    than 30 days from today are skipped with a warning log (no exception raised).
    """
    client = KISClient(
        auth=auth,
        app_key=app_key,
        app_secret=app_secret,
        cano=cano,
        acnt_prdt_cd=acnt_prdt_cd,
        paper=paper,
    )

    today = datetime.now(KST).date()
    cutoff = today - timedelta(days=30)

    trading_days = [
        d.date()
        for d in pd.bdate_range(start, end)
        if not is_krx_holiday(d.date())
    ]

    now = datetime.now(tz=timezone.utc)
    all_records: list[dict] = []
    first_call = True

    for d in trading_days:
        if d < cutoff:
            log.warning("KIS intraday >30d limit, skipping %s", d)
            continue

        if not first_call:
            time.sleep(0.5)
        first_call = False

        bars = fetch_intraday_ohlcv_raw(client, symbol, d.strftime("%Y%m%d"), interval=interval)

        for bar in bars:
            ts = pd.Timestamp(
                f"{bar.date[:4]}-{bar.date[4:6]}-{bar.date[6:8]}"
                f" {bar.time[:2]}:{bar.time[2:4]}:{bar.time[4:6]}",
                tz="Asia/Seoul",
            ).tz_convert("UTC")
            all_records.append({
                "symbol": symbol,
                "ts": ts,
                "freq": f"{interval}m",
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
                "vwap": bar.trade_amt / bar.volume if bar.volume > 0 else 0.0,
                "trade_count": 0,
                "source": "kis",
                "ingested_at": pd.Timestamp(now),
            })

    if not all_records:
        return pd.DataFrame(columns=list(OHLCV_SCHEMA.keys()))

    return pd.DataFrame(all_records)
