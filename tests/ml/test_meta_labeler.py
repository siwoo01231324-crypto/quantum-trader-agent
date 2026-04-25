"""Unit tests for MetaLabeler (src/ml/meta_labeler.py)."""
import numpy as np
import pandas as pd
import pytest

from ml.meta_labeler import MetaLabeler, MetaLabelerConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dataset(n: int = 300, seed: int = 42) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(
        {
            "feat_a": rng.standard_normal(n),
            "feat_b": rng.standard_normal(n),
            "feat_c": rng.standard_normal(n),
        }
    )
    # Labels correlated with feat_a sign to give the model something to learn
    y = pd.Series((X["feat_a"] > 0).astype(int))
    return X, y


def _fast_config() -> MetaLabelerConfig:
    """Minimal config for fast unit-test training."""
    return MetaLabelerConfig(
        num_boost_round=20,
        early_stopping_rounds=10,
        min_data_in_leaf=5,
        random_state=42,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_fit_predict_proba_shape():
    """predict_proba returns (N, 2) array after fit."""
    X, y = _make_dataset(300)
    ml = MetaLabeler(_fast_config()).fit(X, y)
    proba = ml.predict_proba(X)
    assert proba.shape == (len(X), 2)


def test_predict_proba_sums_to_one():
    """Each row of predict_proba sums to 1.0."""
    X, y = _make_dataset(300)
    ml = MetaLabeler(_fast_config()).fit(X, y)
    proba = ml.predict_proba(X)
    np.testing.assert_allclose(proba.sum(axis=1), np.ones(len(X)), atol=1e-6)


def test_win_probability_equals_proba_col1():
    """win_probability == predict_proba[:, 1] — no post-processing."""
    X, y = _make_dataset(300)
    ml = MetaLabeler(_fast_config()).fit(X, y)
    wp = ml.win_probability(X)
    proba = ml.predict_proba(X)
    np.testing.assert_array_equal(wp, proba[:, 1])


def test_deterministic_reproducibility():
    """Same seed → identical predictions on two independent fits."""
    X, y = _make_dataset(300)
    cfg = _fast_config()
    p1 = MetaLabeler(cfg).fit(X, y).predict_proba(X)
    p2 = MetaLabeler(cfg).fit(X, y).predict_proba(X)
    np.testing.assert_array_equal(p1, p2)


def test_save_load_roundtrip(tmp_path):
    """save() + load() produces identical predictions."""
    X, y = _make_dataset(300)
    ml = MetaLabeler(_fast_config()).fit(X, y)
    save_dir = ml.save(tmp_path / "model")

    ml2 = MetaLabeler.load(save_dir)
    np.testing.assert_array_equal(ml.predict_proba(X), ml2.predict_proba(X))


def test_manifest_has_git_sha(tmp_path):
    """manifest.json must include git_sha key."""
    import json
    X, y = _make_dataset(300)
    ml = MetaLabeler(_fast_config()).fit(X, y)
    save_dir = ml.save(tmp_path / "model")
    manifest = json.loads((save_dir / "manifest.json").read_text())
    assert "git_sha" in manifest


def test_feature_importance_returns_series():
    """feature_importance() returns a pd.Series with correct index."""
    X, y = _make_dataset(300)
    ml = MetaLabeler(_fast_config()).fit(X, y)
    imp = ml.feature_importance()
    assert isinstance(imp, pd.Series)
    assert list(imp.index) == list(X.columns)
