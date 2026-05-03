"""Tests for src/data_lake/ohlcv_filter.py."""
from __future__ import annotations

import pandas as pd
import pytest

from src.data_lake.ohlcv_filter import filter_noise_bars, mark_noise_bars
from src.data_lake.schema import validate_schema, OHLCV_SCHEMA


def _make_ohlcv(volumes: list[float]) -> pd.DataFrame:
    n = len(volumes)
    return pd.DataFrame(
        {
            "symbol": ["A"] * n,
            "ts": pd.date_range("2024-01-01", periods=n, freq="1min", tz="UTC"),
            "freq": ["1m"] * n,
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.0] * n,
            "volume": [float(v) for v in volumes],
            "vwap": [100.0] * n,
            "trade_count": [10] * n,
            "source": ["test"] * n,
            "ingested_at": pd.Timestamp("2024-01-01", tz="UTC"),
        }
    )


class TestFilterNoiseBars:
    def test_removes_zero_volume_rows(self):
        df = _make_ohlcv([100.0, 0.0, 50.0, 0.0])
        result = filter_noise_bars(df)
        assert len(result) == 2
        assert (result["volume"] > 0).all()

    def test_keeps_all_when_no_zero_volume(self):
        df = _make_ohlcv([10.0, 20.0, 30.0])
        result = filter_noise_bars(df)
        assert len(result) == 3

    def test_returns_copy_not_view(self):
        df = _make_ohlcv([10.0, 0.0])
        result = filter_noise_bars(df)
        result.iloc[0, result.columns.get_loc("close")] = 999.0
        assert df.iloc[0]["close"] != 999.0

    def test_exclude_zero_volume_false_keeps_zeros(self):
        df = _make_ohlcv([100.0, 0.0])
        result = filter_noise_bars(df, exclude_zero_volume=False)
        assert len(result) == 2

    def test_ohlcv_schema_columns_preserved(self):
        df = _make_ohlcv([10.0, 20.0])
        result = filter_noise_bars(df)
        assert set(result.columns) == set(OHLCV_SCHEMA.keys())


class TestMarkNoiseBars:
    def test_adds_three_sidecar_columns(self):
        df = _make_ohlcv([10.0, 0.0])
        marked = mark_noise_bars(df)
        assert "_is_vi_halt" in marked.columns
        assert "_is_single_price" in marked.columns
        assert "_volume_zero" in marked.columns

    def test_sidecar_columns_are_bool(self):
        df = _make_ohlcv([10.0, 0.0])
        marked = mark_noise_bars(df)
        assert marked["_is_vi_halt"].dtype == bool
        assert marked["_is_single_price"].dtype == bool
        assert marked["_volume_zero"].dtype == bool

    def test_volume_zero_detection(self):
        df = _make_ohlcv([100.0, 0.0, 50.0])
        marked = mark_noise_bars(df)
        assert list(marked["_volume_zero"]) == [False, True, False]

    def test_vi_halt_default_false(self):
        df = _make_ohlcv([10.0, 20.0])
        marked = mark_noise_bars(df)
        assert marked["_is_vi_halt"].all() == False  # noqa: E712

    def test_single_price_default_false(self):
        df = _make_ohlcv([10.0, 20.0])
        marked = mark_noise_bars(df)
        assert marked["_is_single_price"].all() == False  # noqa: E712

    def test_validate_schema_rejects_marked_df(self):
        """validate_schema must flag extra sidecar columns — this is expected behavior."""
        df = _make_ohlcv([10.0])
        marked = mark_noise_bars(df)
        # Build a single record from the first row to test validate_schema
        record = marked.iloc[0].to_dict()
        errors = validate_schema("ohlcv", record)
        assert any("unexpected" in e for e in errors), (
            "validate_schema should reject sidecar columns — "
            "callers must filter_noise_bars() for schema-compliant output"
        )

    def test_filter_output_passes_validate_schema(self):
        """filter_noise_bars output (no sidecar cols) must pass validate_schema."""
        df = _make_ohlcv([10.0, 20.0])
        filtered = filter_noise_bars(df)
        record = filtered.iloc[0].to_dict()
        errors = validate_schema("ohlcv", record)
        assert errors == [], f"Unexpected schema errors: {errors}"
