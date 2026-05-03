"""Tests for KIS cross-validation pipeline (Issue #97, Phase A).

Tests:
  1. Synthetic 2000-bar OHLCV → run_kis_pipeline completes successfully.
     Manifest has costs_bps=26.0 and holding_bars=26.
  2. Mock data with no divergence events → exit_code=3 path returned.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

WORKTREE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKTREE / "src"))

from ml.pipelines.kis_cross_validation import run_kis_pipeline, _make_synthetic_ohlcv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flat_ohlcv(n: int = 100) -> pd.DataFrame:
    """Completely flat price series — no RSI divergence possible."""
    index = pd.date_range("2024-01-02 09:00:00", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "open": np.ones(n) * 50_000.0,
            "high": np.ones(n) * 50_000.0,
            "low": np.ones(n) * 50_000.0,
            "close": np.ones(n) * 50_000.0,
            "volume": np.ones(n) * 1_000_000.0,
        },
        index=index,
    )


# ---------------------------------------------------------------------------
# Test 1: successful run with synthetic data
# ---------------------------------------------------------------------------

def test_run_kis_pipeline_synthetic(tmp_path):
    """2000-bar synthetic OHLCV → pipeline completes; manifest has correct KRX params."""
    ohlcv = _make_synthetic_ohlcv(n=2000, seed=42)
    output_dir = tmp_path / "model_out"

    # Patch load_ohlcv_from_lake to raise FileNotFoundError so pipeline uses synthetic fallback
    with patch("ml.pipelines.kis_cross_validation.load_ohlcv_from_lake", side_effect=FileNotFoundError("no lake")):
        artifact, report = run_kis_pipeline(
            lake_dir=tmp_path / "lake",
            output_dir=output_dir,
            holding_bars=26,
            costs_bps=26.0,
        )

    # Must not return no_events on healthy synthetic data
    assert report.get("status") != "no_events", f"Unexpected no_events: {report}"
    assert report.get("exit_code") != 3, "Should not exit with code 3 on valid data"

    # Artifact must be populated
    assert artifact is not None, "Expected SavedArtifact, got None"
    assert artifact.manifest_path.exists(), "manifest.json not written"

    manifest = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))

    # Core KRX guardrails
    label_cfg = manifest.get("label_config", {})
    assert label_cfg.get("costs_bps") == 26.0, (
        f"Expected costs_bps=26.0, got {label_cfg.get('costs_bps')}"
    )
    assert label_cfg.get("holding_bars") == 26, (
        f"Expected holding_bars=26, got {label_cfg.get('holding_bars')}"
    )
    assert manifest.get("strategy_id") == "momo-kis-v1"


# ---------------------------------------------------------------------------
# Test 2: no divergence events → exit_code=3
# ---------------------------------------------------------------------------

def test_run_kis_pipeline_no_events(tmp_path):
    """Flat OHLCV (no divergence possible) → (None, {exit_code: 3}) returned."""
    flat = _flat_ohlcv(n=100)
    output_dir = tmp_path / "model_out_flat"

    with patch("ml.pipelines.kis_cross_validation.load_ohlcv_from_lake", side_effect=FileNotFoundError("no lake")):
        # Patch _make_synthetic_ohlcv to return flat data
        with patch("ml.pipelines.kis_cross_validation._make_synthetic_ohlcv", return_value=flat):
            artifact, report = run_kis_pipeline(
                lake_dir=tmp_path / "lake",
                output_dir=output_dir,
                holding_bars=26,
                costs_bps=26.0,
            )

    assert artifact is None, f"Expected None artifact for no-events path, got {artifact}"
    assert report.get("exit_code") == 3, f"Expected exit_code=3, got {report}"
    assert report.get("status") == "no_events"
