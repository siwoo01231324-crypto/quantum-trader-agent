"""Factor cache: convert factor outputs into FACTOR_SCHEMA long format and Parquet."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from data_lake.schema import FACTOR_SCHEMA, partition_path

from .registry import DEFAULT_FACTOR_SET


def _to_utc(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if index.tz is None:
        return index.tz_localize("UTC")
    return index.tz_convert("UTC")


def to_factor_long(
    result: pd.Series | pd.DataFrame,
    *,
    symbol: str,
    factor_name: str,
    factor_set: str = DEFAULT_FACTOR_SET,
) -> pd.DataFrame:
    """Melt a factor Series/DataFrame into FACTOR_SCHEMA long format.

    Columns: symbol, ts, factor_set, factor_name, value. Drops rows where
    ``value`` is NaN (typical warmup). DataFrame columns are unpacked into
    ``factor_name = f"{factor_name}.{col}"``.
    """
    if isinstance(result, pd.DataFrame):
        frames = [
            _series_to_long(
                result[col],
                symbol=symbol,
                factor_name=f"{factor_name}.{col}",
                factor_set=factor_set,
            )
            for col in result.columns
        ]
        return pd.concat(frames, ignore_index=True) if frames else _empty_long()

    return _series_to_long(result, symbol=symbol, factor_name=factor_name, factor_set=factor_set)


def _empty_long() -> pd.DataFrame:
    return pd.DataFrame({col: pd.Series(dtype="object") for col in FACTOR_SCHEMA.keys()})


def _series_to_long(
    series: pd.Series,
    *,
    symbol: str,
    factor_name: str,
    factor_set: str,
) -> pd.DataFrame:
    if not isinstance(series.index, pd.DatetimeIndex):
        raise TypeError("factor result must be indexed by DatetimeIndex")

    ts = _to_utc(series.index)
    values = pd.to_numeric(series.to_numpy(), errors="coerce")
    df = pd.DataFrame(
        {
            "symbol": symbol,
            "ts": ts,
            "factor_set": factor_set,
            "factor_name": factor_name,
            "value": values,
        }
    )
    return df.dropna(subset=["value"]).reset_index(drop=True)


def write_factor_parquet(
    df: pd.DataFrame,
    root: Path,
    symbol: str,
    factor_set: str = DEFAULT_FACTOR_SET,
) -> Path:
    """Write factor rows to a Hive-partitioned parquet file.

    Partitions are created per ``(year, month)`` of the ``ts`` column, matching
    ``partition_path("factor", ...)``. Returns the path of the last file written.
    """
    if df.empty:
        raise ValueError("cannot write empty factor DataFrame")
    missing = set(FACTOR_SCHEMA.keys()) - set(df.columns)
    if missing:
        raise ValueError(f"FACTOR_SCHEMA columns missing: {sorted(missing)}")

    root = Path(root)
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)

    last_path: Path | None = None
    for (year, month), chunk in df.groupby([df["ts"].dt.year, df["ts"].dt.month]):
        rel = partition_path(
            "factor", symbol=symbol, ts_year=int(year), ts_month=int(month), factor_set=factor_set
        )
        out_dir = root / rel
        out_dir.mkdir(parents=True, exist_ok=True)
        file_path = out_dir / "part-0.parquet"
        chunk.to_parquet(file_path, index=False)
        last_path = file_path

    assert last_path is not None
    return last_path


def read_factor_parquet(
    root: Path,
    symbol: str,
    factor_set: str = DEFAULT_FACTOR_SET,
    factor_name: str | None = None,
) -> pd.DataFrame:
    """Read all factor files for ``(symbol, factor_set)``, optionally filtered by name."""
    root = Path(root)
    factor_root = root / "factor" / f"factor_set={factor_set}"
    if not factor_root.exists():
        return _empty_long()

    files = sorted(factor_root.rglob(f"symbol={symbol}/*.parquet"))
    if not files:
        return _empty_long()

    frames = [pd.read_parquet(f) for f in files]
    combined = pd.concat(frames, ignore_index=True)
    if factor_name is not None:
        combined = combined[combined["factor_name"] == factor_name].reset_index(drop=True)
    return combined
