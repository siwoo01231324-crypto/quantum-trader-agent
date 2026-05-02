"""Walk-forward 윈도우 비교 + 자동 롤백 임계 분석.

Public API:
    WindowComparisonResult  — 단일 윈도우 fold 집계 결과
    WalkForwardWindowComparator — 4종 윈도우 비교기
    RollbackThresholdAnalyzer   — false positive/negative rate 실측
    load_walkforward_config     — configs/walkforward.yaml 로드
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence
import sys

import numpy as np
import pandas as pd

_WORKTREE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_WORKTREE / "src"))

from ml.walkforward import WalkForwardConfig, WalkForwardSplitter  # noqa: E402


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_walkforward_config(config_path: Path | None = None) -> dict:
    """Load configs/walkforward.yaml. Returns empty dict if file missing."""
    if config_path is None:
        config_path = _WORKTREE / "configs" / "walkforward.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml  # type: ignore[import]
        with config_path.open(encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # pyyaml not available — parse minimal subset manually
        text = config_path.read_text(encoding="utf-8")
        result: dict = {}
        for line in text.splitlines():
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                k, _, v = line.partition(":")
                v = v.strip()
                if v:
                    result[k.strip()] = v
        return result


# ---------------------------------------------------------------------------
# Per-fold metrics
# ---------------------------------------------------------------------------

@dataclass
class FoldMetrics:
    fold: int
    n_train: int
    n_test: int
    accuracy: float
    precision: float
    recall: float
    skipped: bool = False


@dataclass
class WindowComparisonResult:
    """Aggregated walk-forward result for a single training window."""
    window_days: int
    asset: str                          # "btc" | "krx"
    mode: str                           # "rolling" | "expanding"
    n_folds: int
    mean_accuracy: float
    std_accuracy: float
    mean_precision: float
    mean_recall: float
    sharpe_proxy: float                 # accuracy-based Sharpe proxy (mean/std)
    folds: list[FoldMetrics] = field(default_factory=list)

    @property
    def f1(self) -> float:
        if self.mean_precision + self.mean_recall == 0:
            return 0.0
        return 2 * self.mean_precision * self.mean_recall / (self.mean_precision + self.mean_recall)


# ---------------------------------------------------------------------------
# Walk-forward window comparator
# ---------------------------------------------------------------------------

class WalkForwardWindowComparator:
    """Compare multiple training window sizes via walk-forward CV.

    Uses the MetaLabeler's LightGBM fit/predict interface but works with
    any binary classifier that has fit(X, y) and predict_proba(X) -> array.
    Falls back to a LogisticRegression stub when LightGBM is unavailable
    so tests can run without the full ML stack.
    """

    def __init__(
        self,
        window_days: Sequence[int],
        asset: str = "btc",
        mode: str = "rolling",
        bars_per_day: int = 96,          # 15-min bars: 96/day
        test_window_days: int = 7,
        step_days: int = 7,
        min_train_samples: int = 50,
        threshold: float = 0.5,
    ) -> None:
        self.window_days = list(window_days)
        self.asset = asset
        self.mode = mode
        self.bars_per_day = bars_per_day
        self.test_window_days = test_window_days
        self.step_days = step_days
        self.min_train_samples = min_train_samples
        self.threshold = threshold

    def _make_clf(self):
        """Return a fitted-able binary classifier."""
        try:
            from ml.meta_labeler import MetaLabeler
            return MetaLabeler()
        except (ImportError, Exception):
            pass
        try:
            from sklearn.linear_model import LogisticRegression
            return LogisticRegression(max_iter=200, random_state=42)
        except ImportError:
            raise RuntimeError("Neither LightGBM nor scikit-learn available for WalkForwardWindowComparator")

    def _fit_predict(self, clf, X_tr: pd.DataFrame, y_tr: pd.Series, X_te: pd.DataFrame) -> np.ndarray:
        """Return binary predictions for X_te."""
        clf_instance = self._make_clf()
        try:
            # MetaLabeler API
            clf_instance.fit(X_tr, y_tr)
            probs = clf_instance.win_probability(X_te)
        except AttributeError:
            # sklearn API
            clf_instance.fit(X_tr, y_tr)
            probs = clf_instance.predict_proba(X_te)[:, 1]
        return (probs >= self.threshold).astype(int)

    def compare(
        self,
        X: pd.DataFrame,
        y: pd.Series,
    ) -> list[WindowComparisonResult]:
        """Run walk-forward CV for each window size.

        Parameters
        ----------
        X:
            Feature matrix with DatetimeIndex.
        y:
            Binary labels {0, 1} with same index.

        Returns
        -------
        List of WindowComparisonResult, one per window size.
        """
        results: list[WindowComparisonResult] = []
        index = X.index if isinstance(X.index, pd.DatetimeIndex) else pd.RangeIndex(len(X))

        use_timedelta = isinstance(X.index, pd.DatetimeIndex)

        for days in self.window_days:
            if use_timedelta:
                cfg = WalkForwardConfig(
                    mode=self.mode,
                    train_window=pd.Timedelta(days=days),
                    test_window=pd.Timedelta(days=self.test_window_days),
                    step=pd.Timedelta(days=self.step_days),
                    min_train_samples=self.min_train_samples,
                )
            else:
                train_bars = days * self.bars_per_day
                test_bars = self.test_window_days * self.bars_per_day
                step_bars = self.step_days * self.bars_per_day
                cfg = WalkForwardConfig(
                    mode=self.mode,
                    train_window=train_bars,
                    test_window=test_bars,
                    step=step_bars,
                    min_train_samples=self.min_train_samples,
                )
            splitter = WalkForwardSplitter(cfg)

            fold_metrics: list[FoldMetrics] = []
            accuracies: list[float] = []
            precisions: list[float] = []
            recalls: list[float] = []

            for fold_idx, (train_idx, test_idx) in enumerate(splitter.split(index)):  # type: ignore[arg-type]
                if len(train_idx) < self.min_train_samples or len(test_idx) < 5:
                    fold_metrics.append(FoldMetrics(fold=fold_idx, n_train=len(train_idx),
                                                    n_test=len(test_idx), accuracy=0.0,
                                                    precision=0.0, recall=0.0, skipped=True))
                    continue

                X_tr = X.iloc[train_idx]
                y_tr = y.iloc[train_idx]
                X_te = X.iloc[test_idx]
                y_te = y.iloc[test_idx].values

                try:
                    preds = self._fit_predict(None, X_tr, y_tr, X_te)
                except Exception:
                    fold_metrics.append(FoldMetrics(fold=fold_idx, n_train=len(train_idx),
                                                    n_test=len(test_idx), accuracy=0.0,
                                                    precision=0.0, recall=0.0, skipped=True))
                    continue

                acc = float((preds == y_te).mean())
                tp = int(((preds == 1) & (y_te == 1)).sum())
                fp = int(((preds == 1) & (y_te == 0)).sum())
                fn = int(((preds == 0) & (y_te == 1)).sum())
                prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
                rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0

                accuracies.append(acc)
                precisions.append(prec)
                recalls.append(rec)
                fold_metrics.append(FoldMetrics(fold=fold_idx, n_train=len(train_idx),
                                                n_test=len(test_idx), accuracy=acc,
                                                precision=prec, recall=rec))

            if not accuracies:
                results.append(WindowComparisonResult(
                    window_days=days, asset=self.asset, mode=self.mode,
                    n_folds=0, mean_accuracy=0.0, std_accuracy=0.0,
                    mean_precision=0.0, mean_recall=0.0, sharpe_proxy=0.0,
                    folds=fold_metrics,
                ))
                continue

            mean_acc = float(np.mean(accuracies))
            std_acc = float(np.std(accuracies)) if len(accuracies) > 1 else 0.0
            sharpe_proxy = mean_acc / std_acc if std_acc > 0 else mean_acc * 10.0

            results.append(WindowComparisonResult(
                window_days=days, asset=self.asset, mode=self.mode,
                n_folds=len(accuracies),
                mean_accuracy=mean_acc,
                std_accuracy=std_acc,
                mean_precision=float(np.mean(precisions)),
                mean_recall=float(np.mean(recalls)),
                sharpe_proxy=sharpe_proxy,
                folds=fold_metrics,
            ))

        return results

    @staticmethod
    def best_window(results: list[WindowComparisonResult]) -> WindowComparisonResult | None:
        """Select best window by Sharpe proxy (accuracy / std)."""
        valid = [r for r in results if r.n_folds > 0]
        if not valid:
            return None
        return max(valid, key=lambda r: r.sharpe_proxy)


# ---------------------------------------------------------------------------
# Rollback threshold analyzer
# ---------------------------------------------------------------------------

@dataclass
class ThresholdAnalysis:
    threshold: float
    n_rollbacks_triggered: int
    n_true_positives: int      # rollback triggered AND performance actually degraded
    n_false_positives: int     # rollback triggered BUT performance was fine
    n_false_negatives: int     # rollback NOT triggered BUT performance degraded
    n_true_negatives: int
    false_positive_rate: float
    false_negative_rate: float
    precision: float
    recall: float


class RollbackThresholdAnalyzer:
    """Measure rollback false positive/negative rates across threshold candidates.

    Simulates sequential retrain events using walk-forward accuracy series,
    treating a real degradation as accuracy drop > `true_degradation_min`.
    """

    def __init__(
        self,
        true_degradation_min: float = 0.03,
    ) -> None:
        self.true_degradation_min = true_degradation_min

    def analyze(
        self,
        accuracy_series: Sequence[float],
        thresholds: Sequence[float] | None = None,
    ) -> list[ThresholdAnalysis]:
        """Analyze rollback trigger rates for each threshold.

        Parameters
        ----------
        accuracy_series:
            Sequence of CV accuracy values from sequential retrains.
        thresholds:
            Sharpe-equivalent accuracy-delta thresholds to test.
            Defaults to [0.02, 0.05, 0.10, 0.20, 0.30].

        Returns
        -------
        List of ThresholdAnalysis, one per threshold value.
        """
        if thresholds is None:
            thresholds = [0.02, 0.05, 0.10, 0.20, 0.30]

        accs = list(accuracy_series)
        if len(accs) < 2:
            return []

        # Compute pairwise deltas: delta[i] = accs[i-1] - accs[i]  (positive = degradation)
        deltas = [accs[i - 1] - accs[i] for i in range(1, len(accs))]
        true_degraded = [d >= self.true_degradation_min for d in deltas]

        results: list[ThresholdAnalysis] = []
        for thr in thresholds:
            triggered = [d >= thr for d in deltas]

            tp = sum(t and d for t, d in zip(triggered, true_degraded))
            fp = sum(t and not d for t, d in zip(triggered, true_degraded))
            fn = sum(not t and d for t, d in zip(triggered, true_degraded))
            tn = sum(not t and not d for t, d in zip(triggered, true_degraded))

            total_actual_pos = tp + fn
            total_actual_neg = fp + tn

            fpr = fp / total_actual_neg if total_actual_neg > 0 else 0.0
            fnr = fn / total_actual_pos if total_actual_pos > 0 else 0.0
            prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0

            results.append(ThresholdAnalysis(
                threshold=float(thr),
                n_rollbacks_triggered=sum(triggered),
                n_true_positives=tp,
                n_false_positives=fp,
                n_false_negatives=fn,
                n_true_negatives=tn,
                false_positive_rate=fpr,
                false_negative_rate=fnr,
                precision=prec,
                recall=rec,
            ))

        return results

    @staticmethod
    def recommend(analyses: list[ThresholdAnalysis], max_fpr: float = 0.20) -> ThresholdAnalysis | None:
        """Recommend threshold: highest recall s.t. FPR <= max_fpr."""
        candidates = [a for a in analyses if a.false_positive_rate <= max_fpr]
        if not candidates:
            return None
        return max(candidates, key=lambda a: a.recall)
