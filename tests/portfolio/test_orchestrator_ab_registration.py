"""A/B registration tests for on/off MetaLabeler strategies (issue #94)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from risk.dsl import Policy


def _make_policy() -> Policy:
    return Policy(policy_version=1, name="test")


def _make_toy_model_dir(tmp_path: Path, name: str = "model_dir") -> Path:
    import lightgbm as lgb

    model_dir = tmp_path / name
    model_dir.mkdir()

    X = pd.DataFrame({
        "rsi": np.random.rand(80),
        "atr": np.random.rand(80),
        "divergence_magnitude": np.random.rand(80),
        "bars_since_pivot": np.random.randint(1, 20, 80).astype(float),
        "confidence": np.random.rand(80),
        "close": np.random.rand(80) * 50000,
        "volume": np.random.rand(80) * 1e6,
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


def _load_orch(tmp_path: Path, model_dir: Path):
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
    config_path = tmp_path / "prod.yaml"
    config_path.write_text(yaml_content, encoding="utf-8")

    from portfolio.config_loader import load_orchestrator_from_yaml
    return load_orchestrator_from_yaml(config_path, _make_policy())


# ---------------------------------------------------------------------------
# Test 1: both strategy IDs exist in orch._strategies
# ---------------------------------------------------------------------------

def test_both_ids_present(tmp_path):
    model_dir = _make_toy_model_dir(tmp_path)
    orch = _load_orch(tmp_path, model_dir)

    assert "momo-btc-v2" in orch._strategies
    assert "momo-btc-v2-meta" in orch._strategies


# ---------------------------------------------------------------------------
# Test 2: on/off are separate instances (not the same object)
# ---------------------------------------------------------------------------

def test_separate_instances(tmp_path):
    model_dir = _make_toy_model_dir(tmp_path)
    orch = _load_orch(tmp_path, model_dir)

    off_strategy = orch._strategies["momo-btc-v2"]._strategy
    on_strategy = orch._strategies["momo-btc-v2-meta"]._strategy

    assert off_strategy is not on_strategy


# ---------------------------------------------------------------------------
# Test 3: on has MetaLabeler, off has None
# ---------------------------------------------------------------------------

def test_on_off_metalabeler_separation(tmp_path):
    from ml.meta_labeler import MetaLabeler

    model_dir = _make_toy_model_dir(tmp_path)
    orch = _load_orch(tmp_path, model_dir)

    off_strategy = orch._strategies["momo-btc-v2"]._strategy
    on_strategy = orch._strategies["momo-btc-v2-meta"]._strategy

    assert off_strategy._metalabeler is None
    assert isinstance(on_strategy._metalabeler, MetaLabeler)


# ---------------------------------------------------------------------------
# Test 4: duplicate strategy_id raises ValueError
# ---------------------------------------------------------------------------

def test_duplicate_id_raises(tmp_path):
    yaml_content = """
strategies:
  - id: momo-btc-v2
    class: backtest.strategies.momo_btc_v2.MomoBtcV2
    kwargs:
      sizing_mode: full

  - id: momo-btc-v2
    class: backtest.strategies.momo_btc_v2.MomoBtcV2
    kwargs:
      sizing_mode: full
"""
    config_path = tmp_path / "dup.yaml"
    config_path.write_text(yaml_content, encoding="utf-8")

    from portfolio.config_loader import load_orchestrator_from_yaml

    with pytest.raises(ValueError, match="Duplicate strategy_id"):
        load_orchestrator_from_yaml(config_path, _make_policy())


# ---------------------------------------------------------------------------
# Test 5: register_returns init → refresh_portfolio_risk returns None (< 2 obs)
# ---------------------------------------------------------------------------

def test_register_returns_empty_series_refresh_returns_none(tmp_path):
    model_dir = _make_toy_model_dir(tmp_path)
    orch = _load_orch(tmp_path, model_dir)

    # Both strategies were registered with empty Series → portfolio risk = None
    result = orch.refresh_portfolio_risk()
    assert result is None
