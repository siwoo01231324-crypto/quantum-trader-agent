"""Tests for run_kis_pipeline_pooled (C1)."""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ml.pipelines.kis_cross_validation import (
    _make_synthetic_ohlcv,
    run_kis_pipeline_pooled,
)


def _make_ohlcv(seed: int = 42, n: int = 2000) -> pd.DataFrame:
    return _make_synthetic_ohlcv(n=n, seed=seed)


def _mock_load_factory(symbol_map: dict[str, pd.DataFrame | Exception]):
    """Return a mock for load_ohlcv_from_lake that uses symbol_map."""
    def _mock(lake_dir: Path, symbol: str, interval: str) -> pd.DataFrame:
        result = symbol_map.get(symbol, FileNotFoundError(f"No data for {symbol}"))
        if isinstance(result, Exception):
            raise result
        return result
    return _mock


class TestRunKisPipelinePooled:
    def test_three_symbols_success(self, tmp_path):
        """Synthetic 3 symbols → pipeline completes, manifest has correct fields."""
        symbols = ["A", "B", "C"]
        ohlcv_map = {s: _make_ohlcv(seed=i) for i, s in enumerate(symbols)}

        with patch(
            "ml.pipelines.kis_cross_validation.load_ohlcv_from_lake",
            side_effect=_mock_load_factory(ohlcv_map),
        ):
            artifact, report = run_kis_pipeline_pooled(
                symbols=symbols,
                lake_dir=tmp_path / "lake",
                output_dir=tmp_path / "model",
                interval="1m",
                holding_bars=78,
                costs_bps=26.0,
            )

        assert report["status"] == "ok"
        assert report["n_symbols"] == 3
        assert report["holding_bars"] == 78
        assert report["costs_bps"] == 26.0
        assert "rho_avg" in report
        assert "n_eff" in report

        # Verify manifest.json
        manifest_path = tmp_path / "model" / "manifest.json"
        import json
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["n_symbols"] == 3
        assert manifest["holding_bars"] == 78
        assert manifest["costs_bps"] == 26.0

    def test_one_of_three_missing_skips_and_warns(self, tmp_path):
        """1 of 3 symbols missing in lake → 2-symbol pool runs + warning issued."""
        symbols = ["A", "B", "C"]
        ohlcv_map = {
            "A": _make_ohlcv(seed=0),
            "B": _make_ohlcv(seed=1),
            "C": FileNotFoundError("no data"),
        }

        with patch(
            "ml.pipelines.kis_cross_validation.load_ohlcv_from_lake",
            side_effect=_mock_load_factory(ohlcv_map),
        ):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                artifact, report = run_kis_pipeline_pooled(
                    symbols=symbols,
                    lake_dir=tmp_path / "lake",
                    output_dir=tmp_path / "model",
                )

        assert report["status"] == "ok"
        assert report["n_symbols"] == 2
        skip_warnings = [w for w in caught if "skipping" in str(w.message).lower()]
        assert len(skip_warnings) >= 1

    def test_all_symbols_missing_raises_value_error(self, tmp_path):
        """All symbols missing → ValueError raised."""
        symbols = ["X", "Y", "Z"]
        ohlcv_map = {s: FileNotFoundError("no data") for s in symbols}

        with patch(
            "ml.pipelines.kis_cross_validation.load_ohlcv_from_lake",
            side_effect=_mock_load_factory(ohlcv_map),
        ):
            with pytest.raises(ValueError, match="All.*symbols failed"):
                run_kis_pipeline_pooled(
                    symbols=symbols,
                    lake_dir=tmp_path / "lake",
                    output_dir=tmp_path / "model",
                )

    def test_use_time_block_cv_true_uses_time_block(self, tmp_path):
        """use_time_block_cv=True (default) — report reflects the flag."""
        symbols = ["A", "B", "C"]
        ohlcv_map = {s: _make_ohlcv(seed=i) for i, s in enumerate(symbols)}

        with patch(
            "ml.pipelines.kis_cross_validation.load_ohlcv_from_lake",
            side_effect=_mock_load_factory(ohlcv_map),
        ):
            artifact, report = run_kis_pipeline_pooled(
                symbols=symbols,
                lake_dir=tmp_path / "lake",
                output_dir=tmp_path / "model",
                use_time_block_cv=True,
            )
        assert report["use_time_block_cv"] is True
        assert report["status"] == "ok"
