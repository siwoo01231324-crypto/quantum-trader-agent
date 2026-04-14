"""Data lake schema definitions (Polars-compatible, but framework-agnostic).

Schemas are dicts of {column_name: type_string} so they can be validated even
without polars installed.
"""
from __future__ import annotations

from typing import Mapping, Any

# Type aliases (string sentinels — actual polars/pandas mapping happens at edge)
TS = "datetime[us, UTC]"
F64 = "float64"
I64 = "int64"
STR = "string"
CAT = "categorical"
BOOL = "bool"
DATE = "date"
LIST_F64 = "list[float64]"
JSON = "json"

OHLCV_SCHEMA: Mapping[str, str] = {
    "symbol": CAT,
    "ts": TS,
    "freq": CAT,
    "open": F64,
    "high": F64,
    "low": F64,
    "close": F64,
    "volume": F64,
    "vwap": F64,
    "trade_count": I64,
    "source": CAT,
    "ingested_at": TS,
}

ORDERBOOK_L5_SCHEMA: Mapping[str, str] = {
    "symbol": CAT,
    "ts": TS,
    "bid_px": LIST_F64,
    "bid_sz": LIST_F64,
    "ask_px": LIST_F64,
    "ask_sz": LIST_F64,
    "source": CAT,
}

TRADE_SCHEMA: Mapping[str, str] = {
    "symbol": CAT,
    "ts": TS,
    "price": F64,
    "size": F64,
    "side": STR,
    "trade_id": STR,
    "source": CAT,
}

FACTOR_SCHEMA: Mapping[str, str] = {
    "symbol": CAT,
    "ts": TS,
    "factor_set": CAT,
    "factor_name": CAT,
    "value": F64,
}

ASSET_MASTER_SCHEMA: Mapping[str, str] = {
    "symbol": STR,
    "isin": STR,
    "exchange": CAT,
    "asset_type": CAT,
    "ccy": CAT,
    "listed_at": DATE,
    "delisted_at": DATE,
    "name": STR,
}

CORP_ACTION_SCHEMA: Mapping[str, str] = {
    "symbol": STR,
    "ex_date": DATE,
    "action_type": CAT,
    "ratio": F64,
    "meta": JSON,
}

CALENDAR_SCHEMA: Mapping[str, str] = {
    "exchange": CAT,
    "date": DATE,
    "is_open": BOOL,
    "open_ts": TS,
    "close_ts": TS,
}

ALL_SCHEMAS: Mapping[str, Mapping[str, str]] = {
    "ohlcv": OHLCV_SCHEMA,
    "orderbook_l5": ORDERBOOK_L5_SCHEMA,
    "trade": TRADE_SCHEMA,
    "factor": FACTOR_SCHEMA,
    "asset_master": ASSET_MASTER_SCHEMA,
    "corp_action": CORP_ACTION_SCHEMA,
    "calendar": CALENDAR_SCHEMA,
}


def validate_schema(table: str, record: Mapping[str, Any]) -> list[str]:
    """Validate a single record against a named schema.

    Returns a list of error messages; empty list means valid.
    Only checks key presence (missing/extra). Type checking is left to polars
    at write time, since this stub avoids hard dependencies.
    """
    if table not in ALL_SCHEMAS:
        return [f"unknown table '{table}'"]
    schema = ALL_SCHEMAS[table]
    errors: list[str] = []
    missing = set(schema) - set(record)
    extra = set(record) - set(schema)
    if missing:
        errors.append(f"missing columns: {sorted(missing)}")
    if extra:
        errors.append(f"unexpected columns: {sorted(extra)}")
    return errors


def partition_path(table: str, *, symbol: str, ts_year: int, ts_month: int,
                   freq: str | None = None, factor_set: str | None = None,
                   day: int | None = None) -> str:
    """Build a hive-style partition path for the given table."""
    if table == "ohlcv":
        if freq is None:
            raise ValueError("ohlcv requires freq")
        return f"ohlcv/freq={freq}/year={ts_year}/month={ts_month:02d}/symbol={symbol}/"
    if table == "orderbook_l5":
        if day is None:
            raise ValueError("orderbook_l5 requires day")
        return (
            f"orderbook/year={ts_year}/month={ts_month:02d}/"
            f"day={day:02d}/symbol={symbol}/"
        )
    if table == "trade":
        return f"trade/year={ts_year}/month={ts_month:02d}/symbol={symbol}/"
    if table == "factor":
        if factor_set is None:
            raise ValueError("factor requires factor_set")
        return (
            f"factor/factor_set={factor_set}/year={ts_year}/"
            f"month={ts_month:02d}/symbol={symbol}/"
        )
    raise ValueError(f"no partition rule for table '{table}'")
