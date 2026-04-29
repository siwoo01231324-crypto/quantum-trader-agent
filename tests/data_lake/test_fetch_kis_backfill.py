"""Tests for scripts/fetch_kis_backfill.py (mock-based)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# Ensure scripts/ is importable
WORKTREE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKTREE / "scripts"))
sys.path.insert(0, str(WORKTREE / "src"))


def _make_ohlcv_df(n: int = 100) -> pd.DataFrame:
    """Synthetic 100-bar DataFrame matching OHLCV_SCHEMA."""
    import pandas as pd
    from datetime import timezone
    now = pd.Timestamp("2026-04-22 01:00:00", tz="UTC")
    ts = pd.date_range(now, periods=n, freq="1min", tz="UTC")
    return pd.DataFrame({
        "symbol": ["005930"] * n,
        "ts": ts,
        "freq": ["1m"] * n,
        "open": np.full(n, 60000.0),
        "high": np.full(n, 60100.0),
        "low": np.full(n, 59900.0),
        "close": np.full(n, 60050.0),
        "volume": np.full(n, 50000.0),
        "vwap": np.full(n, 60020.0),
        "trade_count": np.zeros(n, dtype=int),
        "source": ["kis"] * n,
        "ingested_at": [now] * n,
    })


# ---------------------------------------------------------------------------
# Dry-run exits 0
# ---------------------------------------------------------------------------

def test_dry_run_exits_zero(tmp_path, capsys):
    """--dry-run exits 0 and prints pool symbols without any API calls."""
    with patch("universe.krx_pool.get_pool_codes", return_value=["005930", "000660", "035720"]):
        import importlib
        import fetch_kis_backfill
        importlib.reload(fetch_kis_backfill)

        args = fetch_kis_backfill._make_args(
            ["--dry-run", "--n-symbols", "3", "--lake-dir", str(tmp_path)]
        )
        rc = fetch_kis_backfill._run_dry(args)

    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "005930" in out


# ---------------------------------------------------------------------------
# Missing KIS credentials → graceful exit 0
# ---------------------------------------------------------------------------

def test_missing_credentials_graceful_exit(tmp_path, capsys):
    """KISAuth raises on missing env → exit 0 with guidance message."""
    import importlib
    import fetch_kis_backfill
    importlib.reload(fetch_kis_backfill)

    args = fetch_kis_backfill._make_args(
        ["--n-symbols", "3", "--lake-dir", str(tmp_path)]
    )

    with patch.dict("os.environ", {}, clear=True):
        # Ensure no KIS env vars are present
        for key in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_CANO", "KIS_ACNT_PRDT_CD"):
            import os
            os.environ.pop(key, None)

        rc = fetch_kis_backfill._run_live(args)

    assert rc == 0
    err = capsys.readouterr().err
    assert "KIS_TOKEN env not set" in err or "Skipping backfill" in err


# ---------------------------------------------------------------------------
# Mock fetch → lake partition written
# ---------------------------------------------------------------------------

def test_mock_fetch_writes_lake_partition(tmp_path):
    """Mock fetch_kis_intraday_ohlcv returns 100-bar df → lake partition file written."""
    import importlib
    import fetch_kis_backfill
    importlib.reload(fetch_kis_backfill)

    synthetic_df = _make_ohlcv_df(100)

    args = fetch_kis_backfill._make_args(
        ["--n-symbols", "1", "--interval", "1m", "--lake-dir", str(tmp_path)]
    )

    mock_auth = MagicMock()

    with patch("fetch_kis_backfill.fetch_kis_intraday_ohlcv", return_value=synthetic_df), \
         patch("fetch_kis_backfill.save_ohlcv_parquet", wraps=_spy_save(tmp_path)) as mock_save:
        fetch_kis_backfill._fetch_one(
            symbol="005930",
            start="2026-04-22",
            end="2026-04-22",
            args=args,
            auth=mock_auth,
            app_key="key",
            app_secret="secret",
            cano="12345678",
            acnt_prdt_cd="01",
        )
        assert mock_save.called


def _spy_save(tmp_path: Path):
    """Wrap save_ohlcv_parquet so it actually writes files to tmp_path."""
    from data_lake.fetcher import save_ohlcv_parquet as real_save

    def _wrapped(df, lake_dir, symbol, freq):
        return real_save(df, tmp_path, symbol, freq)

    return _wrapped
