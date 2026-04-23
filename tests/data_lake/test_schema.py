"""Schema validation tests for the data lake."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from data_lake.schema import (  # noqa: E402
    ALL_SCHEMAS,
    OHLCV_SCHEMA,
    FACTOR_SCHEMA,
    validate_schema,
    partition_path,
)


def _ohlcv_record():
    return {
        "symbol": "005930",
        "ts": "2026-01-02T00:00:00Z",
        "freq": "1d",
        "open": 70000.0,
        "high": 71000.0,
        "low": 69500.0,
        "close": 70500.0,
        "volume": 12_345_678.0,
        "vwap": 70250.0,
        "trade_count": 9876,
        "source": "krx",
        "ingested_at": "2026-01-02T18:00:00Z",
    }


def test_ohlcv_record_is_valid():
    assert validate_schema("ohlcv", _ohlcv_record()) == []


def test_ohlcv_record_missing_column():
    rec = _ohlcv_record()
    del rec["close"]
    errs = validate_schema("ohlcv", rec)
    assert len(errs) == 1
    assert "missing columns" in errs[0]
    assert "close" in errs[0]


def test_ohlcv_record_extra_column():
    rec = _ohlcv_record()
    rec["adj_close"] = 70500.0
    errs = validate_schema("ohlcv", rec)
    assert any("unexpected columns" in e and "adj_close" in e for e in errs)


def test_unknown_table():
    errs = validate_schema("does_not_exist", {})
    assert errs == ["unknown table 'does_not_exist'"]


def test_factor_schema_keys():
    assert set(FACTOR_SCHEMA) == {
        "symbol", "ts", "factor_set", "factor_name", "value",
    }


def test_partition_path_ohlcv():
    p = partition_path("ohlcv", symbol="005930", ts_year=2026,
                       ts_month=1, freq="1d")
    assert p == "ohlcv/freq=1d/year=2026/month=01/symbol=005930/"


def test_partition_path_factor_requires_factor_set():
    import pytest
    with pytest.raises(ValueError):
        partition_path("factor", symbol="005930", ts_year=2026, ts_month=1)


def test_all_schemas_have_symbol_or_exchange():
    for name, schema in ALL_SCHEMAS.items():
        assert "symbol" in schema or "exchange" in schema, name


def test_ohlcv_has_required_ohlcv_columns():
    for c in ("open", "high", "low", "close", "volume"):
        assert c in OHLCV_SCHEMA
