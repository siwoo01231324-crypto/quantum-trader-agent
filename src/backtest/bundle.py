from pathlib import Path
import pandas as pd


def load_ohlcv_from_parquet(
    data_dir: Path,
    symbol: str,
    freq: str,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Read partitioned Parquet files from data_dir/ohlcv/freq={freq}/year=*/month=*/symbol={symbol}/*.parquet"""
    base = Path(data_dir) / "ohlcv" / f"freq={freq}"
    frames = []
    if base.exists():
        for parquet_file in sorted(base.rglob(f"symbol={symbol}/*.parquet")):
            frames.append(pd.read_parquet(parquet_file))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.sort_values("ts").set_index("ts")
    if start:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df.index <= pd.Timestamp(end, tz="UTC")]
    return df
