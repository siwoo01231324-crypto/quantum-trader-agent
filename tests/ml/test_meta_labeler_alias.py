"""Tests for MetaLabeler.load() pointer.json alias resolution (1-hop)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.meta_labeler import MetaLabeler, MetaLabelerConfig


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _fast_config() -> MetaLabelerConfig:
    return MetaLabelerConfig(
        num_boost_round=5,
        early_stopping_rounds=3,
        min_data_in_leaf=1,
        random_state=0,
    )


def _make_dataset(n: int = 60, seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({
        "f1": rng.standard_normal(n),
        "f2": rng.standard_normal(n),
    })
    y = pd.Series((X["f1"] > 0).astype(int))
    return X, y


@pytest.fixture(scope="module")
def saved_model_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Train a tiny booster and save it; return the versioned directory."""
    import lightgbm as lgb

    tmp = tmp_path_factory.mktemp("ml_alias")
    X, y = _make_dataset()

    # Build toy booster via lgb.train (1 iteration) to avoid fitting overhead
    params = {
        "objective": "binary",
        "num_leaves": 4,
        "min_data_in_leaf": 1,
        "verbosity": -1,
        "random_state": 0,
    }
    ds = lgb.Dataset(X, label=y)
    booster = lgb.train(params, ds, num_boost_round=1)

    version_dir = tmp / "models" / "momo-btc-v2" / "20260424-000000"
    version_dir.mkdir(parents=True)
    booster.save_model(str(version_dir / "model.lgbm"))
    manifest = {
        "trained_at": "2026-04-24T00:00:00+00:00",
        "git_sha": "deadbeef",
        "feature_names": list(X.columns),
        "config": {
            "num_boost_round": 5,
            "early_stopping_rounds": 3,
            "learning_rate": 0.05,
            "num_leaves": 31,
            "min_data_in_leaf": 1,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "lambda_l2": 0.1,
            "random_state": 0,
        },
    }
    (version_dir / "manifest.json").write_text(json.dumps(manifest))
    return version_dir


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestMetaLabelerAlias:
    def test_direct_load(self, saved_model_dir: Path) -> None:
        """(a) Direct load from versioned directory works and returns predictions."""
        X, _ = _make_dataset()
        ml = MetaLabeler.load(saved_model_dir)
        proba = ml.predict_proba(X)
        assert proba.shape == (len(X), 2)

    def test_alias_load_matches_direct(self, saved_model_dir: Path, tmp_path: Path) -> None:
        """(b) Alias directory via pointer.json produces identical predictions to direct load."""
        X, _ = _make_dataset()
        direct = MetaLabeler.load(saved_model_dir)

        # Create latest/ alias in same parent
        latest_dir = saved_model_dir.parent / "latest"
        latest_dir.mkdir(exist_ok=True)
        pointer = {"active": saved_model_dir.name, "promoted_at": "2026-04-24T00:00:00+00:00", "git_sha": "deadbeef"}
        (latest_dir / "pointer.json").write_text(json.dumps(pointer))

        alias = MetaLabeler.load(latest_dir)
        np.testing.assert_array_equal(direct.predict_proba(X), alias.predict_proba(X))

    def test_self_reference_raises(self, saved_model_dir: Path, tmp_path: Path) -> None:
        """(c) pointer.json pointing to itself raises ValueError."""
        self_dir = tmp_path / "self_alias"
        self_dir.mkdir()
        pointer = {"active": "self_alias", "promoted_at": "2026-04-24T00:00:00+00:00", "git_sha": "abc"}
        (self_dir / "pointer.json").write_text(json.dumps(pointer))

        with pytest.raises((ValueError, RecursionError)):
            MetaLabeler.load(self_dir)

    def test_invalid_active_key_raises(self, saved_model_dir: Path, tmp_path: Path) -> None:
        """(d) pointer.json with non-existent active directory raises FileNotFoundError."""
        alias_dir = tmp_path / "broken_alias"
        alias_dir.mkdir()
        pointer = {"active": "nonexistent-version", "promoted_at": "2026-04-24T00:00:00+00:00", "git_sha": "abc"}
        (alias_dir / "pointer.json").write_text(json.dumps(pointer))

        with pytest.raises(FileNotFoundError):
            MetaLabeler.load(alias_dir)
