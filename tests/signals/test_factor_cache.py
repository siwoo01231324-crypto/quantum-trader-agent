"""Tests for src/signals/cache.py — FACTOR_SCHEMA long-format + Parquet round-trip."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest


def _ts_index(n: int = 20, start: str = "2024-01-01", freq: str = "15min") -> pd.DatetimeIndex:
    return pd.date_range(start=start, periods=n, freq=freq)


def test_to_factor_long_schema_series():
    from data_lake.schema import FACTOR_SCHEMA
    from signals.cache import to_factor_long

    ts = _ts_index(10)
    values = pd.Series(np.arange(10.0), index=ts)
    df = to_factor_long(values, symbol="BTCUSDT", factor_name="rsi")
    assert set(df.columns) == set(FACTOR_SCHEMA.keys())
    assert len(df) == 10
    assert (df["symbol"] == "BTCUSDT").all()
    assert (df["factor_name"] == "rsi").all()
    assert (df["factor_set"] == "v1").all()  # DEFAULT_FACTOR_SET
    assert list(df["value"]) == list(np.arange(10.0))


def test_to_factor_long_dataframe_melts_columns():
    """DataFrame input becomes multiple rows per timestamp (one per column)."""
    from signals.cache import to_factor_long

    ts = _ts_index(5)
    frame = pd.DataFrame(
        {"macd": np.arange(5.0), "signal": np.arange(5.0) * 2, "histogram": np.arange(5.0) * 3},
        index=ts,
    )
    out = to_factor_long(frame, symbol="BTCUSDT", factor_name="macd")
    # 5 timestamps x 3 columns = 15 rows; factor_name becomes "macd.<col>"
    assert len(out) == 15
    names = set(out["factor_name"].unique())
    assert names == {"macd.macd", "macd.signal", "macd.histogram"}


def test_to_factor_long_drops_nan_values():
    from signals.cache import to_factor_long

    ts = _ts_index(5)
    values = pd.Series([np.nan, np.nan, 3.0, 4.0, 5.0], index=ts)
    df = to_factor_long(values, symbol="BTCUSDT", factor_name="rsi")
    assert len(df) == 3
    assert list(df["value"]) == [3.0, 4.0, 5.0]


def test_to_factor_long_ts_utc_enforced():
    """Naive DatetimeIndex must be localized to UTC before schema write."""
    from signals.cache import to_factor_long

    ts = pd.date_range("2024-01-01", periods=5, freq="15min")  # tz-naive
    values = pd.Series([1.0, 2, 3, 4, 5], index=ts)
    df = to_factor_long(values, symbol="BTCUSDT", factor_name="rsi")
    assert df["ts"].dt.tz is not None, "ts must carry a timezone after to_factor_long"
    assert str(df["ts"].dt.tz) == "UTC"


def test_write_read_roundtrip(tmp_path: Path):
    from signals.cache import read_factor_parquet, to_factor_long, write_factor_parquet

    ts = _ts_index(30)
    values = pd.Series(np.arange(30.0), index=ts)
    df = to_factor_long(values, symbol="BTCUSDT", factor_name="rsi")

    root = tmp_path / "lake"
    path = write_factor_parquet(df, root, symbol="BTCUSDT")
    assert path.exists()

    loaded = read_factor_parquet(root, symbol="BTCUSDT", factor_name="rsi")
    assert len(loaded) == len(df)
    assert list(loaded["value"]) == list(df["value"])


def test_write_read_roundtrip_filter_by_name(tmp_path: Path):
    """Write two factors, read back only one."""
    from signals.cache import read_factor_parquet, to_factor_long, write_factor_parquet

    ts = _ts_index(20)
    rsi = to_factor_long(pd.Series(np.arange(20.0), index=ts), symbol="BTCUSDT", factor_name="rsi")
    sma = to_factor_long(pd.Series(np.arange(20.0) * 2, index=ts), symbol="BTCUSDT", factor_name="sma")

    combined = pd.concat([rsi, sma], ignore_index=True)
    root = tmp_path / "lake"
    write_factor_parquet(combined, root, symbol="BTCUSDT")

    only_rsi = read_factor_parquet(root, symbol="BTCUSDT", factor_name="rsi")
    assert (only_rsi["factor_name"] == "rsi").all()
    assert len(only_rsi) == 20


def test_write_uses_partition_path(tmp_path: Path):
    """Output path matches partition_path('factor', ...) convention."""
    from data_lake.schema import partition_path
    from signals.cache import to_factor_long, write_factor_parquet

    ts = pd.date_range("2024-03-15", periods=5, freq="15min", tz="UTC")
    df = to_factor_long(pd.Series([1.0, 2, 3, 4, 5], index=ts), symbol="BTCUSDT", factor_name="rsi")

    root = tmp_path / "lake"
    path = write_factor_parquet(df, root, symbol="BTCUSDT")

    expected_partition = partition_path(
        "factor", symbol="BTCUSDT", ts_year=2024, ts_month=3, factor_set="v1"
    )
    assert str(path).replace("\\", "/").endswith(expected_partition + path.name)
