"""Schema validation tests for the data lake (extended for FUNDAMENTALS_PIT_SCHEMA)."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_lake.schema import (  # noqa: E402
    ALL_SCHEMAS,
    FUNDAMENTALS_PIT_SCHEMA,
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
    with pytest.raises(ValueError):
        partition_path("factor", symbol="005930", ts_year=2026, ts_month=1)


def test_all_schemas_have_symbol_or_exchange():
    for name, schema in ALL_SCHEMAS.items():
        assert "symbol" in schema or "exchange" in schema, name


def test_ohlcv_has_required_ohlcv_columns():
    for c in ("open", "high", "low", "close", "volume"):
        assert c in OHLCV_SCHEMA


# ---------------------------------------------------------------------------
# FUNDAMENTALS_PIT_SCHEMA (issue #74)
# ---------------------------------------------------------------------------

def test_fundamentals_pit_registered():
    """ALL_SCHEMAS must contain 'fundamentals' key with exactly 9 columns."""
    assert "fundamentals" in ALL_SCHEMAS
    schema = ALL_SCHEMAS["fundamentals"]
    expected_columns = {
        "symbol", "announce_date", "period_end", "fiscal_period",
        "metric", "value", "unit", "source", "ingested_at",
    }
    assert set(schema.keys()) == expected_columns
    assert len(schema) == 9


def test_fundamentals_pit_schema_types():
    """Column types in FUNDAMENTALS_PIT_SCHEMA must use correct sentinels."""
    s = FUNDAMENTALS_PIT_SCHEMA
    assert s["symbol"] == "categorical"
    assert s["value"] == "float64"
    assert "datetime" in s["announce_date"]
    assert "datetime" in s["period_end"]
    assert "datetime" in s["ingested_at"]


def test_partition_path_fundamentals():
    """fundamentals partition path must match hive-style pattern."""
    p = partition_path("fundamentals", symbol="005930", ts_year=2026, ts_month=3)
    pattern = r"^fundamentals/symbol=005930/year=2026/month=03/$"
    assert re.match(pattern, p), f"Got: {p!r}"


def test_partition_path_fundamentals_month_zero_padded():
    p = partition_path("fundamentals", symbol="000660", ts_year=2026, ts_month=1)
    assert "month=01" in p


def test_fundamentals_validate_valid_record():
    record = {
        "symbol": "005930",
        "announce_date": "2026-03-31T00:00:00+09:00",
        "period_end": "2026-03-31T00:00:00+09:00",
        "fiscal_period": "202603",
        "metric": "per",
        "value": 14.52,
        "unit": "ratio",
        "source": "kis_fin_ratio_v1",
        "ingested_at": "2026-04-24T00:00:00Z",
    }
    assert validate_schema("fundamentals", record) == []
