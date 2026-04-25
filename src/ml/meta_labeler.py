"""LightGBM 2차 메타라벨러 — 기본 전략 신호의 false positive 필터."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
import pandas as pd


@dataclass
class MetaLabelerConfig:
    num_boost_round: int = 500
    early_stopping_rounds: int = 50
    learning_rate: float = 0.05
    num_leaves: int = 31
    min_data_in_leaf: int = 50
    feature_fraction: float = 0.8
    bagging_fraction: float = 0.8
    bagging_freq: int = 5
    lambda_l2: float = 0.1
    random_state: int = 42


class MetaLabeler:
    def __init__(self, config: MetaLabelerConfig = MetaLabelerConfig()) -> None:
        self.config = config
        self._booster: "lgb.Booster | None" = None  # type: ignore[name-defined]
        self._feature_names: list[str] = []

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def fit(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: "pd.DataFrame | None" = None,
        y_val: "pd.Series | None" = None,
    ) -> "MetaLabeler":
        """Fit the LightGBM booster.

        Parameters
        ----------
        X_train:
            Training features.
        y_train:
            Binary labels {0, 1} from triple_barrier_label.
        X_val:
            Optional validation features for early stopping.
        y_val:
            Optional validation labels.

        Returns
        -------
        self
        """
        import lightgbm as lgb

        self._feature_names = list(X_train.columns)
        cfg = self.config

        params: dict = {
            "objective": "binary",
            "metric": "binary_logloss",
            "learning_rate": cfg.learning_rate,
            "num_leaves": cfg.num_leaves,
            "min_data_in_leaf": cfg.min_data_in_leaf,
            "feature_fraction": cfg.feature_fraction,
            "bagging_fraction": cfg.bagging_fraction,
            "bagging_freq": cfg.bagging_freq,
            "lambda_l2": cfg.lambda_l2,
            # Reproducibility invariant (CLAUDE.md #6)
            "deterministic": True,
            "force_col_wise": True,
            "random_state": cfg.random_state,
            "seed": cfg.random_state,
            "verbosity": -1,
        }

        train_set = lgb.Dataset(X_train, label=y_train)
        valid_sets = [train_set]
        callbacks: list = [lgb.log_evaluation(period=-1)]

        if X_val is not None and y_val is not None:
            val_set = lgb.Dataset(X_val, label=y_val, reference=train_set)
            valid_sets = [train_set, val_set]
            callbacks.append(
                lgb.early_stopping(stopping_rounds=cfg.early_stopping_rounds, verbose=False)
            )

        self._booster = lgb.train(
            params,
            train_set,
            num_boost_round=cfg.num_boost_round,
            valid_sets=valid_sets,
            callbacks=callbacks,
        )
        return self

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Return class probabilities, shape (N, 2).

        Column 0: P(label=0), column 1: P(label=1).
        """
        if self._booster is None:
            raise RuntimeError("MetaLabeler has not been fitted. Call fit() first.")
        p1 = self._booster.predict(X[self._feature_names])
        p0 = 1.0 - p1
        return np.column_stack([p0, p1])

    def win_probability(self, X: pd.DataFrame) -> np.ndarray:
        """Return P(win) — predict_proba[:, 1] with no post-processing."""
        return self.predict_proba(X)[:, 1]

    # ------------------------------------------------------------------
    # Feature importance
    # ------------------------------------------------------------------

    def feature_importance(self, method: str = "permutation") -> pd.Series:
        """Return feature importances as a named Series.

        Parameters
        ----------
        method:
            ``"gain"``, ``"split"``, or ``"permutation"`` (alias for ``"gain"``).
        """
        if self._booster is None:
            raise RuntimeError("MetaLabeler has not been fitted.")
        lgb_method = "gain" if method == "permutation" else method
        importances = self._booster.feature_importance(importance_type=lgb_method)
        return pd.Series(importances, index=self._feature_names, name="importance")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, dir_path: Path) -> Path:
        """Save model + manifest to ``dir_path``. Returns the directory."""
        import json
        import subprocess
        from dataclasses import asdict
        from datetime import datetime, timezone

        if self._booster is None:
            raise RuntimeError("MetaLabeler has not been fitted.")

        dir_path = Path(dir_path)
        dir_path.mkdir(parents=True, exist_ok=True)

        model_path = dir_path / "model.lgbm"
        self._booster.save_model(str(model_path))

        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            git_sha = result.stdout.strip() if result.returncode == 0 else "unknown"
        except Exception:
            git_sha = "unknown"

        manifest = {
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "git_sha": git_sha,
            "feature_names": self._feature_names,
            "config": asdict(self.config),
        }
        (dir_path / "manifest.json").write_text(json.dumps(manifest, indent=2))
        return dir_path

    @classmethod
    def load(cls, dir_path: Path) -> "MetaLabeler":
        """Load a saved MetaLabeler from ``dir_path``."""
        import json
        import lightgbm as lgb

        dir_path = Path(dir_path)
        model_path = dir_path / "model.lgbm"
        manifest_path = dir_path / "manifest.json"

        if not model_path.exists():
            raise FileNotFoundError(f"model.lgbm not found in {dir_path}")
        if not manifest_path.exists():
            raise FileNotFoundError(f"manifest.json not found in {dir_path}")

        manifest = json.loads(manifest_path.read_text())
        config = MetaLabelerConfig(**manifest.get("config", {}))

        instance = cls(config=config)
        instance._booster = lgb.Booster(model_file=str(model_path))
        instance._feature_names = manifest.get("feature_names", [])
        return instance
