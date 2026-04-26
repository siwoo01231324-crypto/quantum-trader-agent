"""Tests for src/portfolio/config_loader.py (issue #94)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from risk.dsl import Policy


def _make_policy() -> Policy:
    return Policy(policy_version=1, name="test")


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "test.yaml"
    p.write_text(content, encoding="utf-8")
    return p


def _make_toy_model_dir(tmp_path: Path, name: str = "model_dir") -> Path:
    """Create a minimal model directory that MetaLabeler.load accepts (toy booster)."""
    import lightgbm as lgb
    import numpy as np

    model_dir = tmp_path / name
    model_dir.mkdir()

    X = pd.DataFrame({
        "rsi": np.random.rand(100),
        "atr": np.random.rand(100),
        "divergence_magnitude": np.random.rand(100),
        "bars_since_pivot": np.random.randint(1, 20, 100).astype(float),
        "confidence": np.random.rand(100),
        "close": np.random.rand(100) * 50000,
        "volume": np.random.rand(100) * 1e6,
    })
    y = pd.Series((X["rsi"] > 0.5).astype(int))

    ds = lgb.Dataset(X, label=y)
    booster = lgb.train(
        {"objective": "binary", "verbosity": -1, "num_leaves": 4, "min_data_in_leaf": 1},
        ds,
        num_boost_round=3,
    )
    booster.save_model(str(model_dir / "model.lgbm"))

    manifest = {
        "trained_at": "2026-04-25T00:00:00+00:00",
        "git_sha": "abc1234",
        "feature_names": list(X.columns),
        "config": {},
    }
    (model_dir / "manifest.json").write_text(json.dumps(manifest))
    return model_dir


# ---------------------------------------------------------------------------
# Test 1: two strategies registered (momo-btc-v2 + momo-btc-v2-meta)
# ---------------------------------------------------------------------------

def test_two_strategies_registered(tmp_path):
    model_dir = _make_toy_model_dir(tmp_path)
    yaml_content = f"""
strategies:
  - id: momo-btc-v2
    class: backtest.strategies.momo_btc_v2.MomoBtcV2
    kwargs:
      sizing_mode: full

  - id: momo-btc-v2-meta
    class: backtest.strategies.momo_btc_v2.MomoBtcV2
    kwargs:
      sizing_mode: full
      metalabeler:
        load_path: {model_dir.as_posix()}
      metalabeler_threshold: 0.5
"""
    config_path = _write_yaml(tmp_path, yaml_content)
    from portfolio.config_loader import load_orchestrator_from_yaml

    orch = load_orchestrator_from_yaml(config_path, _make_policy())

    assert "momo-btc-v2" in orch._strategies
    assert "momo-btc-v2-meta" in orch._strategies
    assert len(orch._strategies) == 2


# ---------------------------------------------------------------------------
# Test 2: metalabeler instance vs None
# ---------------------------------------------------------------------------

def test_metalabeler_instance_vs_none(tmp_path):
    model_dir = _make_toy_model_dir(tmp_path)
    yaml_content = f"""
strategies:
  - id: momo-off
    class: backtest.strategies.momo_btc_v2.MomoBtcV2
    kwargs:
      sizing_mode: full

  - id: momo-on
    class: backtest.strategies.momo_btc_v2.MomoBtcV2
    kwargs:
      sizing_mode: full
      metalabeler:
        load_path: {model_dir.as_posix()}
      metalabeler_threshold: 0.5
"""
    config_path = _write_yaml(tmp_path, yaml_content)
    from portfolio.config_loader import load_orchestrator_from_yaml
    from ml.meta_labeler import MetaLabeler

    orch = load_orchestrator_from_yaml(config_path, _make_policy())

    off_adapter = orch._strategies["momo-off"]
    on_adapter = orch._strategies["momo-on"]

    assert off_adapter._strategy._metalabeler is None
    assert isinstance(on_adapter._strategy._metalabeler, MetaLabeler)


# ---------------------------------------------------------------------------
# Test 3: invalid class path raises ImportError
# ---------------------------------------------------------------------------

def test_import_string_error(tmp_path):
    yaml_content = """
strategies:
  - id: bad
    class: nonexistent.module.DoesNotExist
    kwargs: {}
"""
    config_path = _write_yaml(tmp_path, yaml_content)
    from portfolio.config_loader import load_orchestrator_from_yaml

    with pytest.raises(ImportError):
        load_orchestrator_from_yaml(config_path, _make_policy())


# ---------------------------------------------------------------------------
# Test 4: metalabeler load_path absent → RuntimeError (fail-fast)
# ---------------------------------------------------------------------------

def test_load_path_missing_raises(tmp_path):
    yaml_content = """
strategies:
  - id: meta
    class: backtest.strategies.momo_btc_v2.MomoBtcV2
    kwargs:
      metalabeler:
        load_path: /nonexistent/path/that/does/not/exist
"""
    config_path = _write_yaml(tmp_path, yaml_content)
    from portfolio.config_loader import load_orchestrator_from_yaml

    with pytest.raises(RuntimeError, match="MetaLabeler.load"):
        load_orchestrator_from_yaml(config_path, _make_policy())
