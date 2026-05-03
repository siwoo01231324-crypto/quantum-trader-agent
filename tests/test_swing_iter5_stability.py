"""Tests for scripts/bench_swing_iter5_stability.py (issue #99 iter 5).

Validates:
  - Parameter grid generation (81 combos)
  - Single combo execution produces valid metrics
  - Distribution stats computation
  - Robustness CV classification
  - Smoke mode end-to-end
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts.bench_swing_iter5_stability import (  # noqa: E402
    PARAM_GRID,
    ComboResult,
    compute_distribution_stats,
    compute_metrics,
    generate_grid,
    run_combo,
    synthetic_ohlcv,
    resample_ohlcv,
)


# -- Grid generation -----------------------------------------------------------


class TestGridGeneration:
    def test_grid_count(self) -> None:
        """Grid should produce 3^4 = 81 combinations."""
        grid = generate_grid()
        assert len(grid) == 81

    def test_grid_keys(self) -> None:
        """Every combo should have the 4 expected param keys."""
        grid = generate_grid()
        expected_keys = {"entry_lookback", "exit_lookback", "vol_target", "vol_lookback"}
        for combo in grid:
            assert set(combo.keys()) == expected_keys

    def test_grid_values_in_range(self) -> None:
        """Every param value should come from the defined grid."""
        grid = generate_grid()
        for combo in grid:
            for key, val in combo.items():
                assert val in PARAM_GRID[key], f"{key}={val} not in PARAM_GRID"

    def test_grid_unique(self) -> None:
        """No duplicate combos."""
        grid = generate_grid()
        as_tuples = [tuple(sorted(c.items())) for c in grid]
        assert len(set(as_tuples)) == 81


# -- Combo execution -----------------------------------------------------------


@pytest.fixture
def smoke_df() -> pd.DataFrame:
    """90-day synthetic OHLCV resampled to 4h."""
    start = pd.Timestamp("2023-01-01", tz="UTC")
    df = synthetic_ohlcv(start=start, n_bars=90 * 24 * 60)
    return resample_ohlcv(df, "4h")


class TestRunCombo:
    def test_baseline_params(self, smoke_df: pd.DataFrame) -> None:
        """Iter4 baseline params should produce valid metrics."""
        params = {
            "entry_lookback": 20,
            "exit_lookback": 10,
            "vol_target": 0.15,
            "vol_lookback": 60,
        }
        result = run_combo(params, smoke_df)
        assert result.status == "ok"
        assert result.sharpe is not None
        assert result.mdd is not None
        assert result.mdd <= 0.0

    def test_extreme_params(self, smoke_df: pd.DataFrame) -> None:
        """Smallest grid params should still produce a result."""
        params = {
            "entry_lookback": 10,
            "exit_lookback": 5,
            "vol_target": 0.10,
            "vol_lookback": 10,
        }
        result = run_combo(params, smoke_df)
        assert result.status in ("ok", "no_signal")

    def test_result_is_combo_result(self, smoke_df: pd.DataFrame) -> None:
        params = {
            "entry_lookback": 20,
            "exit_lookback": 10,
            "vol_target": 0.15,
            "vol_lookback": 30,
        }
        result = run_combo(params, smoke_df)
        assert isinstance(result, ComboResult)
        assert result.params == params


# -- Distribution stats --------------------------------------------------------


class TestDistributionStats:
    def test_known_values(self) -> None:
        """Test with a known simple distribution."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        stats = compute_distribution_stats(values)
        assert stats["mean"] == pytest.approx(3.0)
        assert stats["min"] == pytest.approx(1.0)
        assert stats["max"] == pytest.approx(5.0)
        assert stats["median"] == pytest.approx(3.0)
        assert stats["pct_within_1_5_iqr"] == pytest.approx(1.0)

    def test_empty_list(self) -> None:
        stats = compute_distribution_stats([])
        assert stats["mean"] is None
        assert stats["cv"] is None

    def test_single_value(self) -> None:
        stats = compute_distribution_stats([42.0])
        assert stats["mean"] == pytest.approx(42.0)
        assert stats["std"] == pytest.approx(0.0)
        assert stats["min"] == pytest.approx(42.0)
        assert stats["max"] == pytest.approx(42.0)

    def test_cv_computation(self) -> None:
        """CV = std / |mean|."""
        values = [10.0, 20.0, 30.0]
        stats = compute_distribution_stats(values)
        expected_cv = float(np.std(values, ddof=1) / abs(np.mean(values)))
        assert stats["cv"] == pytest.approx(expected_cv)

    def test_iqr_outlier_detection(self) -> None:
        """Values far outside IQR should reduce pct_within_1_5_iqr."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 100.0]
        stats = compute_distribution_stats(values)
        assert stats["pct_within_1_5_iqr"] < 1.0


# -- Smoke end-to-end ----------------------------------------------------------


class TestSmokeEndToEnd:
    def test_smoke_run(self, tmp_path: Path) -> None:
        """Full smoke run should produce a valid JSON output."""
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "bench_swing_iter5_stability.py"),
                "--smoke",
                "--output-dir", str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

        out_file = tmp_path / "bench_output_iter5_grid.json"
        assert out_file.exists()

        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert data["schema_version"] == "swing-strategy-99/v5-grid"
        assert data["iteration"] == 5
        assert data["n_combinations"] == 81
        assert data["n_valid"] > 0
        assert data["robustness"]["verdict"] in ("ROBUST", "MODERATE", "OVER-TUNED")
        assert "sharpe" in data["distribution"]
        assert "mdd" in data["distribution"]
        assert "monthly_hit_rate" in data["distribution"]
        assert len(data["all_combos"]) == 81
