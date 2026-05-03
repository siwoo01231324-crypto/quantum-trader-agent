"""Tests for run_cv_extended in src/ml/retrain_pipeline.py."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml.retrain_pipeline import run_cv, run_cv_extended


# ---------------------------------------------------------------------------
# Synthetic dataset helpers
# ---------------------------------------------------------------------------

def _make_synthetic_cv_data(n: int = 500, seed: int = 42):
    """Return (X, y, t1) suitable for PurgedKFold CV tests."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-01", periods=n, freq="1h", tz="UTC")

    X = pd.DataFrame(
        {
            "feat_a": rng.standard_normal(n),
            "feat_b": rng.standard_normal(n),
            "feat_c": rng.standard_normal(n),
        },
        index=idx,
    )
    # Simple learnable signal: label correlated with feat_a
    y = pd.Series((X["feat_a"] > 0).astype(int), index=idx)

    # t1: each event expires 10 bars later (no overlap at fold boundaries)
    t1 = pd.Series(idx.shift(10), index=idx)

    return X, y, t1


# ---------------------------------------------------------------------------
# run_cv_extended basic structure
# ---------------------------------------------------------------------------

def test_run_cv_extended_returns_expected_keys():
    X, y, t1 = _make_synthetic_cv_data()
    result = run_cv_extended(X, y, t1, n_splits=3, embargo=0.01)
    for key in ("mean_accuracy", "std_accuracy", "n_folds", "embargo_frac", "mean_pr_auc", "folds"):
        assert key in result, f"Missing key: {key}"


def test_run_cv_extended_fold_has_y_true_y_prob():
    X, y, t1 = _make_synthetic_cv_data()
    result = run_cv_extended(X, y, t1, n_splits=3, embargo=0.01)
    non_skipped = [f for f in result["folds"] if not f.get("skipped")]
    assert len(non_skipped) > 0, "Expected at least one non-skipped fold"
    for fold in non_skipped:
        assert "y_true" in fold
        assert "y_prob" in fold
        assert "accuracy" in fold
        assert "pr_auc" in fold
        assert len(fold["y_true"]) == len(fold["y_prob"])
        assert len(fold["y_true"]) == fold["test"]


def test_run_cv_extended_y_prob_in_unit_interval():
    X, y, t1 = _make_synthetic_cv_data()
    result = run_cv_extended(X, y, t1, n_splits=3, embargo=0.01)
    for fold in result["folds"]:
        if fold.get("skipped"):
            continue
        probs = fold["y_prob"]
        assert np.all(probs >= 0.0) and np.all(probs <= 1.0), \
            "y_prob must be in [0, 1]"


def test_run_cv_extended_pr_auc_in_unit_interval():
    X, y, t1 = _make_synthetic_cv_data()
    result = run_cv_extended(X, y, t1, n_splits=3, embargo=0.01)
    for fold in result["folds"]:
        if fold.get("skipped"):
            continue
        assert 0.0 <= fold["pr_auc"] <= 1.0


def test_run_cv_extended_accuracy_matches_run_cv():
    """Accuracies from run_cv_extended must match run_cv fold-by-fold (regression guard)."""
    X, y, t1 = _make_synthetic_cv_data(n=500, seed=99)

    result_orig = run_cv(X, y, t1, n_splits=3, embargo=0.01)
    result_ext = run_cv_extended(X, y, t1, n_splits=3, embargo=0.01)

    assert result_orig["mean_accuracy"] == pytest.approx(result_ext["mean_accuracy"], abs=1e-6), (
        f"mean_accuracy mismatch: {result_orig['mean_accuracy']} vs {result_ext['mean_accuracy']}"
    )
    assert result_orig["n_folds"] == result_ext["n_folds"]

    # Compare per-fold accuracies
    orig_accs = [f["accuracy"] for f in result_orig["folds"] if not f.get("skipped")]
    ext_accs = [f["accuracy"] for f in result_ext["folds"] if not f.get("skipped")]
    assert len(orig_accs) == len(ext_accs)
    for oa, ea in zip(orig_accs, ext_accs):
        assert oa == pytest.approx(ea, abs=1e-9), f"Per-fold accuracy mismatch: {oa} vs {ea}"


def test_run_cv_extended_empty_input_returns_empty_folds():
    """Empty X/y/t1 → empty folds list, mean_accuracy=0.0."""
    idx = pd.DatetimeIndex([], tz="UTC")
    X = pd.DataFrame(columns=["feat_a"], index=idx)
    y = pd.Series([], dtype=int, index=idx)
    t1 = pd.Series([], dtype="datetime64[ns, UTC]", index=idx)

    result = run_cv_extended(X, y, t1, n_splits=3, embargo=0.01)
    assert result["mean_accuracy"] == 0.0
    assert result["folds"] == []
    assert result["n_folds"] == 0


def test_run_cv_extended_mean_pr_auc_in_unit_interval():
    X, y, t1 = _make_synthetic_cv_data()
    result = run_cv_extended(X, y, t1, n_splits=3, embargo=0.01)
    assert 0.0 <= result["mean_pr_auc"] <= 1.0


def test_run_cv_original_unchanged():
    """run_cv still returns original dict structure (BTC regression guard)."""
    X, y, t1 = _make_synthetic_cv_data()
    result = run_cv(X, y, t1, n_splits=3, embargo=0.01)
    for key in ("mean_accuracy", "std_accuracy", "n_folds", "embargo_frac", "folds"):
        assert key in result
    # Original run_cv does NOT have mean_pr_auc
    assert "mean_pr_auc" not in result
    # Original folds do NOT have y_prob
    for fold in result["folds"]:
        if not fold.get("skipped"):
            assert "y_prob" not in fold
