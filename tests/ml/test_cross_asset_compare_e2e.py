"""End-to-end integration test for #155.

Validates:
1. ``run_kis_pipeline_pooled`` persists ``manifest.json`` with a schema
   compatible with ``cross_asset_compare`` — including the
   ``training_window.start``/``end`` fields added in this issue.
2. ``scripts/cross_asset_compare.py`` loads BTC + KIS manifests and renders
   the full Phase A report (5 mandatory sections, no pending placeholder).
3. Graceful fallback to ``보류 (인프라)`` placeholder when KIS manifest is
   absent — preserves regression behavior.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ml.pipelines.kis_cross_validation import (  # noqa: E402
    _make_synthetic_ohlcv,
    run_kis_pipeline_pooled,
)


MD_REQUIRED_HEADERS = [
    "## 데이터 가용성",
    "## 성능 비교표",
    "## DSR 기반 가설 판정 (Phase A)",
    "## 신뢰도 한계",
    "## 결론 및 후속 조치",
]


def _load_cross_asset_compare_module():
    """Load scripts/cross_asset_compare.py as a module (no __init__.py in scripts/)."""
    script_path = ROOT / "scripts" / "cross_asset_compare.py"
    spec = importlib.util.spec_from_file_location("cross_asset_compare_script", script_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _write_btc_manifest(model_dir: Path, *, n_events: int = 50, mean_acc: float = 0.70) -> Path:
    """Mimic train_and_save BTC manifest shape."""
    model_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "strategy_id": "momo-btc-v2",
        "trained_at": "2026-05-01T12:00:00Z",
        "feature_names": ["a", "b"],
        "label_config": {"tp_sigma": 2.0, "sl_sigma": 1.5, "holding_bars": 24, "costs_bps": 4.0},
        "cv_score": {
            "mean_accuracy": mean_acc,
            "std_accuracy": 0.03,
            "n_folds": 5,
            "embargo_frac": 0.01,
        },
        "holdout_accuracy": mean_acc - 0.02,
        "training_window": {
            "start": "2025-04-01 00:00:00+00:00",
            "end":   "2026-04-01 00:00:00+00:00",
            "n_events": n_events,
        },
        "positive_rate_train": 0.42,
    }
    path = model_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def _write_kis_pooled_manifest(
    model_dir: Path, *, n_events: int = 60, mean_acc: float = 0.68,
    n_symbols: int = 10, rho_avg: float = 0.10,
) -> Path:
    """Mimic post-fix KIS pooled manifest shape (with training_window.start/end)."""
    model_dir.mkdir(parents=True, exist_ok=True)
    n_eff = n_symbols / (1.0 + (n_symbols - 1) * rho_avg)
    manifest = {
        "strategy_id": "momo-kis-v1-pooled",
        "n_symbols": n_symbols,
        "symbols": [f"S{i}" for i in range(n_symbols)],
        "rho_avg": round(rho_avg, 6),
        "n_eff": round(n_eff, 4),
        "interval": "1m",
        "holding_bars": 78,
        "costs_bps": 26.0,
        "use_time_block_cv": True,
        "cv_score": {
            "mean_accuracy": mean_acc,
            "std_accuracy": 0.04,
            "n_folds": 5,
        },
        "label_config": {"tp_sigma": 2.0, "sl_sigma": 1.5, "holding_bars": 78, "costs_bps": 26.0},
        "training_window": {
            "start": "2026-03-01 09:00:00+00:00",
            "end":   "2026-04-30 15:30:00+00:00",
            "n_events": n_events,
        },
        "positive_rate_train": 0.41,
    }
    path = model_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. KIS pooled manifest schema
# ---------------------------------------------------------------------------


class TestKisManifestSchema:
    """``run_kis_pipeline_pooled`` must persist manifest.json with all
    fields ``cross_asset_compare`` reads."""

    def test_manifest_includes_training_window_start_end(self, tmp_path: Path) -> None:
        symbols = ["S1", "S2", "S3"]
        synth = {sym: _make_synthetic_ohlcv(n=600, seed=42 + i) for i, sym in enumerate(symbols)}

        def _fake_loader(lake_dir, symbol, interval):
            if symbol not in synth:
                raise FileNotFoundError(symbol)
            return synth[symbol]

        out_dir = tmp_path / "kis-manifest"
        with patch(
            "ml.pipelines.kis_cross_validation.load_ohlcv_from_lake",
            side_effect=_fake_loader,
        ):
            artifact, report = run_kis_pipeline_pooled(
                symbols=symbols,
                lake_dir=tmp_path / "lake",
                output_dir=out_dir,
                interval="15m",
                holding_bars=20,
                costs_bps=26.0,
            )

        if report.get("exit_code") == 3:
            pytest.skip(
                f"Synthetic data produced no triple-barrier events for symbols={symbols}; "
                "GBM seeds didn't yield bullish divergences. Schema-only test, ML accuracy not asserted."
            )

        manifest_path = out_dir / "manifest.json"
        assert manifest_path.exists(), "Pipeline must persist manifest.json under output_dir"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        # Top-level fields cross_asset_compare reads
        for key in ("cv_score", "training_window", "n_symbols", "rho_avg", "n_eff", "label_config"):
            assert key in manifest, f"manifest missing top-level field: {key!r}"

        tw = manifest["training_window"]
        assert "n_events" in tw, "training_window.n_events required (existing)"

        # Issue #155 fix — start/end must be present so cross_asset_compare can
        # populate the data_window column instead of falling back to "N/A".
        assert "start" in tw, "training_window.start required (post-#155 fix)"
        assert "end" in tw, "training_window.end required (post-#155 fix)"
        assert tw["start"], "training_window.start must not be empty"
        assert tw["end"], "training_window.end must not be empty"


# ---------------------------------------------------------------------------
# 2. cross_asset_compare e2e (manifests → 02_implementation.md)
# ---------------------------------------------------------------------------


class TestCrossAssetCompareE2E:
    """E2E: hand-craft both manifests → cross_asset_compare → 02_implementation.md."""

    def test_loads_both_manifests_and_renders_full_report(self, tmp_path: Path) -> None:
        cac = _load_cross_asset_compare_module()

        btc_root = tmp_path / "models" / "momo-btc-v2"
        kis_root = tmp_path / "models" / "momo-kis-v1-pooled"
        _write_btc_manifest(btc_root / "20260501T120000Z", n_events=50)
        _write_kis_pooled_manifest(kis_root / "20260506T120000Z", n_events=60)

        output = tmp_path / "out" / "02_implementation.md"
        rc = cac.main([
            "--btc-model-dir", str(btc_root),
            "--kis-model-dir", str(kis_root),
            "--output", str(output),
        ])
        assert rc == 0
        assert output.exists()

        content = output.read_text(encoding="utf-8")

        # AC: 5 mandatory sections present
        for header in MD_REQUIRED_HEADERS:
            assert header in content, f"Missing section: {header}"

        # AC: no placeholder traces — manifests were loaded, not pending
        assert "manifest not found locally" not in content
        assert "보류 (인프라)" not in content

        # AC: both asset rows present in comparison table
        assert "btc-usdt" in content
        assert "krx-005930" in content

        # AC: data_window populated from manifest.training_window.start/end
        # BTC: start=2025-04-01 → "2025-04 ~ 2026-04"
        assert "2025-04" in content, "BTC data_window must derive from manifest.start/end"
        # KIS: start=2026-03-01 → "2026-03 ~ 2026-04"
        assert "2026-03" in content, "KIS data_window must derive from manifest.start/end"


# ---------------------------------------------------------------------------
# 3. Graceful fallback regression
# ---------------------------------------------------------------------------


class TestGracefulFallback:
    """Regression: empty model dir → '보류 (인프라)' placeholder still works."""

    def test_missing_kis_manifest_uses_pending_placeholder(self, tmp_path: Path) -> None:
        cac = _load_cross_asset_compare_module()

        btc_root = tmp_path / "models" / "momo-btc-v2"
        empty_kis = tmp_path / "models" / "non-existent-kis"
        _write_btc_manifest(btc_root / "20260501T120000Z", n_events=50)

        output = tmp_path / "out" / "02_implementation.md"
        rc = cac.main([
            "--btc-model-dir", str(btc_root),
            "--kis-model-dir", str(empty_kis),
            "--output", str(output),
        ])
        assert rc == 0
        content = output.read_text(encoding="utf-8")

        # 5 sections still rendered
        for header in MD_REQUIRED_HEADERS:
            assert header in content

        # Placeholder note recorded for missing KIS manifest
        assert "manifest not found locally" in content or "N/A" in content
        # Verdict falls back to 보류 (insufficient data on KIS side)
        assert "보류" in content
