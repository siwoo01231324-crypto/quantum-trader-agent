"""Unit tests for scripts/kis_lake_monitor.py (#152).

The monitor must scan the hive-partitioned KIS lake (written by
scripts/cron_fetch_kis_daily.py) and emit a markdown summary suitable for
weekly Telegram digest. The lake layout matches data_lake.fetcher.save_ohlcv_parquet:
    lake/ohlcv/freq={freq}/year=YYYY/month=MM/symbol=SYMBOL/part-0.parquet
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def _load_monitor():
    """Load scripts/kis_lake_monitor.py as a module."""
    script_path = ROOT / "scripts" / "kis_lake_monitor.py"
    spec = importlib.util.spec_from_file_location("kis_lake_monitor_script", script_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _write_partition(
    lake_dir: Path,
    *,
    symbol: str,
    interval: str,
    timestamps: list[pd.Timestamp],
) -> Path:
    """Write a synthetic parquet partition mimicking save_ohlcv_parquet output.

    Groups timestamps by year/month and writes one part-0.parquet per group.
    Returns the lake_dir for chaining.
    """
    df = pd.DataFrame({
        "ts": timestamps,
        "open": [100.0] * len(timestamps),
        "high": [101.0] * len(timestamps),
        "low": [99.0] * len(timestamps),
        "close": [100.5] * len(timestamps),
        "volume": [1000.0] * len(timestamps),
    })
    if df.empty:
        return lake_dir
    ts_col = pd.to_datetime(df["ts"], utc=True)
    df["ts"] = ts_col
    groups = df.groupby([ts_col.dt.year, ts_col.dt.month])
    for (year, month), group in groups:
        part_dir = (
            lake_dir / "ohlcv" / f"freq={interval}"
            / f"year={year}" / f"month={int(month):02d}"
            / f"symbol={symbol}"
        )
        part_dir.mkdir(parents=True, exist_ok=True)
        group.to_parquet(part_dir / "part-0.parquet", index=False)
    return lake_dir


def _krx_session_minutes(date: datetime) -> list[pd.Timestamp]:
    """Return 1-minute UTC timestamps for a single KRX trading session
    (09:00-15:30 KST = 00:00-06:30 UTC, 390 bars per day)."""
    base = pd.Timestamp(date.year, date.month, date.day, 0, 0, 0, tz="UTC")
    return [base + pd.Timedelta(minutes=i) for i in range(390)]


# ---------------------------------------------------------------------------
# scan_lake — per-symbol stats
# ---------------------------------------------------------------------------


class TestScanLake:
    def test_empty_lake_returns_empty_df(self, tmp_path: Path) -> None:
        mon = _load_monitor()
        df = mon.scan_lake(tmp_path, interval="1m")
        assert isinstance(df, pd.DataFrame)
        assert df.empty
        # Schema columns must still be present even when empty
        for col in ("symbol", "n_bars", "n_days", "first_ts", "last_ts"):
            assert col in df.columns, f"missing column {col!r}"

    def test_single_symbol_single_day(self, tmp_path: Path) -> None:
        mon = _load_monitor()
        ts = _krx_session_minutes(datetime(2026, 5, 4, tzinfo=timezone.utc))
        _write_partition(tmp_path, symbol="005930", interval="1m", timestamps=ts)

        df = mon.scan_lake(tmp_path, interval="1m")
        assert len(df) == 1
        row = df.iloc[0]
        assert row["symbol"] == "005930"
        assert row["n_bars"] == 390
        assert row["n_days"] == 1
        assert pd.Timestamp(row["last_ts"]) == ts[-1]

    def test_multi_symbol_multi_day_aggregate(self, tmp_path: Path) -> None:
        mon = _load_monitor()
        # 3 symbols × 5 trading days
        days = [datetime(2026, 5, d, tzinfo=timezone.utc) for d in (4, 5, 6, 7, 8)]
        for sym in ("005930", "000660", "035720"):
            timestamps = [t for d in days for t in _krx_session_minutes(d)]
            _write_partition(tmp_path, symbol=sym, interval="1m", timestamps=timestamps)

        df = mon.scan_lake(tmp_path, interval="1m")
        assert len(df) == 3
        assert set(df["symbol"]) == {"005930", "000660", "035720"}
        for _, row in df.iterrows():
            assert row["n_bars"] == 390 * 5
            assert row["n_days"] == 5

    def test_partial_day_partition_counted(self, tmp_path: Path) -> None:
        """Half-day partition (e.g. mid-fetch failure) still counts as 1 day."""
        mon = _load_monitor()
        full = _krx_session_minutes(datetime(2026, 5, 4, tzinfo=timezone.utc))
        half = full[:200]
        _write_partition(tmp_path, symbol="005930", interval="1m", timestamps=half)
        df = mon.scan_lake(tmp_path, interval="1m")
        assert df.iloc[0]["n_bars"] == 200
        assert df.iloc[0]["n_days"] == 1


# ---------------------------------------------------------------------------
# aggregate_stats — global summary
# ---------------------------------------------------------------------------


class TestAggregateStats:
    def test_empty_aggregate(self, tmp_path: Path) -> None:
        mon = _load_monitor()
        df = mon.scan_lake(tmp_path, interval="1m")
        agg = mon.aggregate_stats(df, target_days=90)
        assert agg["n_symbols"] == 0
        assert agg["total_bars"] == 0
        assert agg["max_days"] == 0
        assert agg["last_ts"] is None
        assert agg["progress_pct"] == 0.0

    def test_aggregate_progress(self, tmp_path: Path) -> None:
        mon = _load_monitor()
        # 2 symbols × 9 days → 9/90 = 10% progress
        days = [datetime(2026, 5, d, tzinfo=timezone.utc) for d in range(1, 10)]
        for sym in ("005930", "000660"):
            ts = [t for d in days for t in _krx_session_minutes(d)]
            _write_partition(tmp_path, symbol=sym, interval="1m", timestamps=ts)

        df = mon.scan_lake(tmp_path, interval="1m")
        agg = mon.aggregate_stats(df, target_days=90)
        assert agg["n_symbols"] == 2
        assert agg["total_bars"] == 2 * 9 * 390
        assert agg["max_days"] == 9
        assert agg["progress_pct"] == pytest.approx(10.0, rel=1e-3)
        assert agg["last_ts"] is not None


# ---------------------------------------------------------------------------
# render_markdown — telegram digest format
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def test_required_sections(self, tmp_path: Path) -> None:
        mon = _load_monitor()
        days = [datetime(2026, 5, d, tzinfo=timezone.utc) for d in range(1, 6)]
        for sym in ("005930", "000660"):
            ts = [t for d in days for t in _krx_session_minutes(d)]
            _write_partition(tmp_path, symbol=sym, interval="1m", timestamps=ts)
        df = mon.scan_lake(tmp_path, interval="1m")
        agg = mon.aggregate_stats(df, target_days=90)
        md = mon.render_markdown(df, agg)

        for header in ("KIS 1분봉 누적 모니터", "진척도", "종목별 누적"):
            assert header in md, f"missing section: {header!r}"
        # Both symbols must appear
        assert "005930" in md
        assert "000660" in md

    def test_empty_lake_renders_zero_state(self, tmp_path: Path) -> None:
        mon = _load_monitor()
        df = mon.scan_lake(tmp_path, interval="1m")
        agg = mon.aggregate_stats(df, target_days=90)
        md = mon.render_markdown(df, agg)
        assert "KIS 1분봉 누적 모니터" in md
        # Either "0/90" or "데이터 없음" — but no traceback / None
        assert "None" not in md
        assert "Traceback" not in md


# ---------------------------------------------------------------------------
# CLI — main entrypoint
# ---------------------------------------------------------------------------


class TestMainCLI:
    def test_main_writes_markdown_to_out(self, tmp_path: Path) -> None:
        mon = _load_monitor()
        days = [datetime(2026, 5, d, tzinfo=timezone.utc) for d in (4, 5, 6)]
        ts = [t for d in days for t in _krx_session_minutes(d)]
        _write_partition(tmp_path, symbol="005930", interval="1m", timestamps=ts)

        out = tmp_path / "out" / "monitor.md"
        rc = mon.main([
            "--lake-dir", str(tmp_path),
            "--interval", "1m",
            "--out", str(out),
        ])
        assert rc == 0
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "005930" in content
        # n_bars value present (3 days × 390 bars = 1,170, formatted with thousands sep)
        assert "1,170" in content
