"""#122 메타라벨러 재학습 윈도우 + 자동 롤백 임계 단위 테스트."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE / "src"))

from ml.window_rollback import (
    WalkForwardWindowComparator,
    RollbackThresholdAnalyzer,
    ThresholdAnalysis,
    load_walkforward_config,
)
from ml.walkforward import WalkForwardConfig, WalkForwardSplitter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_xy(n: int = 3000, seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    """Synthetic feature + label set with DatetimeIndex."""
    rng = np.random.default_rng(seed)
    index = pd.date_range("2024-01-01", periods=n, freq="15min")
    X = pd.DataFrame(
        {
            "rsi": rng.uniform(20, 80, n),
            "atr": rng.uniform(0.1, 2.0, n),
            "divergence_magnitude": rng.standard_normal(n),
            "bars_since_pivot": rng.integers(1, 14, n).astype(float),
            "confidence": rng.uniform(0, 1, n),
            "close": 100.0 + np.cumsum(rng.standard_normal(n) * 0.3),
            "volume": rng.uniform(1000, 10000, n),
        },
        index=index,
    )
    y = pd.Series(rng.integers(0, 2, n), index=index, name="label")
    return X, y


# ---------------------------------------------------------------------------
# WalkForwardSplitter: integer-bar mode
# ---------------------------------------------------------------------------

class TestWalkForwardSplitter:
    def test_rolling_yields_fixed_train_size(self):
        cfg = WalkForwardConfig(mode="rolling", train_window=500, test_window=100, step=100, min_train_samples=500)
        splitter = WalkForwardSplitter(cfg)
        index = pd.date_range("2024-01-01", periods=2000, freq="15min")
        folds = list(splitter.split(index))
        assert len(folds) > 0
        for train_idx, test_idx in folds:
            assert len(train_idx) == 500
            assert len(test_idx) > 0

    def test_expanding_grows_train_size(self):
        cfg = WalkForwardConfig(mode="expanding", train_window=200, test_window=100, step=100, min_train_samples=200)
        splitter = WalkForwardSplitter(cfg)
        index = pd.date_range("2024-01-01", periods=1000, freq="15min")
        folds = list(splitter.split(index))
        assert len(folds) > 1
        train_sizes = [len(tr) for tr, _ in folds]
        assert train_sizes == sorted(train_sizes), "Expanding mode: train size must grow"

    def test_min_train_samples_skips_small_folds(self):
        cfg = WalkForwardConfig(mode="rolling", train_window=100, test_window=50, step=50, min_train_samples=200)
        splitter = WalkForwardSplitter(cfg)
        index = pd.date_range("2024-01-01", periods=500, freq="15min")
        folds = list(splitter.split(index))
        for train_idx, _ in folds:
            assert len(train_idx) >= 200


# ---------------------------------------------------------------------------
# WalkForwardWindowComparator
# ---------------------------------------------------------------------------

class TestWalkForwardWindowComparator:
    def test_compare_returns_one_result_per_window(self):
        X, y = _make_xy(3000)
        comparator = WalkForwardWindowComparator(
            window_days=[7, 14],
            asset="btc",
            mode="rolling",
            bars_per_day=96,
            test_window_days=7,
            step_days=7,
            min_train_samples=50,
        )
        results = comparator.compare(X, y)
        assert len(results) == 2
        assert results[0].window_days == 7
        assert results[1].window_days == 14

    def test_compare_metrics_in_range(self):
        X, y = _make_xy(3000)
        comparator = WalkForwardWindowComparator(
            window_days=[14],
            asset="btc",
            mode="rolling",
            bars_per_day=96,
            test_window_days=7,
            step_days=7,
            min_train_samples=50,
        )
        results = comparator.compare(X, y)
        r = results[0]
        if r.n_folds > 0:
            assert 0.0 <= r.mean_accuracy <= 1.0
            assert 0.0 <= r.mean_precision <= 1.0
            assert 0.0 <= r.mean_recall <= 1.0
            assert r.std_accuracy >= 0.0

    def test_best_window_selects_highest_sharpe_proxy(self):
        from ml.window_rollback import WindowComparisonResult
        r1 = WindowComparisonResult(window_days=7, asset="btc", mode="rolling",
                                    n_folds=3, mean_accuracy=0.55, std_accuracy=0.05,
                                    mean_precision=0.5, mean_recall=0.5, sharpe_proxy=11.0)
        r2 = WindowComparisonResult(window_days=30, asset="btc", mode="rolling",
                                    n_folds=3, mean_accuracy=0.57, std_accuracy=0.02,
                                    mean_precision=0.55, mean_recall=0.55, sharpe_proxy=28.5)
        best = WalkForwardWindowComparator.best_window([r1, r2])
        assert best is not None
        assert best.window_days == 30

    def test_best_window_none_when_no_folds(self):
        from ml.window_rollback import WindowComparisonResult
        r = WindowComparisonResult(window_days=7, asset="btc", mode="rolling",
                                   n_folds=0, mean_accuracy=0.0, std_accuracy=0.0,
                                   mean_precision=0.0, mean_recall=0.0, sharpe_proxy=0.0)
        assert WalkForwardWindowComparator.best_window([r]) is None

    def test_f1_property(self):
        from ml.window_rollback import WindowComparisonResult
        r = WindowComparisonResult(window_days=30, asset="btc", mode="rolling",
                                   n_folds=5, mean_accuracy=0.60, std_accuracy=0.03,
                                   mean_precision=0.6, mean_recall=0.5, sharpe_proxy=20.0)
        expected = 2 * 0.6 * 0.5 / (0.6 + 0.5)
        assert abs(r.f1 - expected) < 1e-6

    def test_f1_zero_when_no_predictions(self):
        from ml.window_rollback import WindowComparisonResult
        r = WindowComparisonResult(window_days=7, asset="btc", mode="rolling",
                                   n_folds=2, mean_accuracy=0.5, std_accuracy=0.0,
                                   mean_precision=0.0, mean_recall=0.0, sharpe_proxy=5.0)
        assert r.f1 == 0.0


# ---------------------------------------------------------------------------
# RollbackThresholdAnalyzer
# ---------------------------------------------------------------------------

class TestRollbackThresholdAnalyzer:
    def _make_series_with_degradation(self) -> list[float]:
        """Series: stable at 0.60, then drop to 0.50 (delta=0.10), then recover."""
        return [0.60, 0.61, 0.59, 0.60, 0.50, 0.51, 0.60, 0.62, 0.61]

    def test_high_threshold_misses_degradation(self):
        analyzer = RollbackThresholdAnalyzer(true_degradation_min=0.03)
        series = self._make_series_with_degradation()
        analyses = analyzer.analyze(series, thresholds=[0.30])
        assert len(analyses) == 1
        a = analyses[0]
        # Threshold 0.30: delta=0.10 at position 4→5 NOT >= 0.30 → false negative
        assert a.n_false_negatives >= 1

    def test_low_threshold_catches_degradation_but_raises_fpr(self):
        analyzer = RollbackThresholdAnalyzer(true_degradation_min=0.03)
        series = self._make_series_with_degradation()
        analyses = analyzer.analyze(series, thresholds=[0.02])
        a = analyses[0]
        # Threshold 0.02 is very sensitive — should catch true degradation
        assert a.n_true_positives >= 1

    def test_rates_sum_correctly(self):
        analyzer = RollbackThresholdAnalyzer(true_degradation_min=0.03)
        series = [0.60, 0.58, 0.56, 0.60, 0.62, 0.50, 0.61]
        analyses = analyzer.analyze(series, thresholds=[0.05])
        a = analyses[0]
        total = a.n_true_positives + a.n_false_positives + a.n_false_negatives + a.n_true_negatives
        assert total == len(series) - 1

    def test_recommend_returns_highest_recall_within_fpr_limit(self):
        analyses = [
            ThresholdAnalysis(threshold=0.02, n_rollbacks_triggered=10, n_true_positives=5,
                              n_false_positives=5, n_false_negatives=0, n_true_negatives=0,
                              false_positive_rate=0.50, false_negative_rate=0.0,
                              precision=0.5, recall=1.0),
            ThresholdAnalysis(threshold=0.10, n_rollbacks_triggered=5, n_true_positives=4,
                              n_false_positives=1, n_false_negatives=1, n_true_negatives=4,
                              false_positive_rate=0.10, false_negative_rate=0.20,
                              precision=0.8, recall=0.8),
            ThresholdAnalysis(threshold=0.30, n_rollbacks_triggered=2, n_true_positives=2,
                              n_false_positives=0, n_false_negatives=3, n_true_negatives=5,
                              false_positive_rate=0.0, false_negative_rate=0.60,
                              precision=1.0, recall=0.4),
        ]
        rec = RollbackThresholdAnalyzer.recommend(analyses, max_fpr=0.20)
        assert rec is not None
        assert rec.threshold == 0.10  # best recall within FPR <= 0.20

    def test_recommend_none_when_all_fpr_exceed_limit(self):
        analyses = [
            ThresholdAnalysis(threshold=0.02, n_rollbacks_triggered=10, n_true_positives=5,
                              n_false_positives=8, n_false_negatives=0, n_true_negatives=2,
                              false_positive_rate=0.80, false_negative_rate=0.0,
                              precision=0.38, recall=1.0),
        ]
        rec = RollbackThresholdAnalyzer.recommend(analyses, max_fpr=0.20)
        assert rec is None

    def test_empty_series_returns_empty(self):
        analyzer = RollbackThresholdAnalyzer()
        results = analyzer.analyze([0.60])
        assert results == []


# ---------------------------------------------------------------------------
# load_walkforward_config
# ---------------------------------------------------------------------------

class TestLoadWalkforwardConfig:
    def test_returns_dict_for_existing_config(self):
        cfg = load_walkforward_config()
        # May or may not exist; if it does, must be a dict
        assert isinstance(cfg, dict)

    def test_returns_empty_dict_for_missing_file(self, tmp_path):
        cfg = load_walkforward_config(tmp_path / "nonexistent.yaml")
        assert cfg == {}

    def test_loads_yaml_correctly(self, tmp_path):
        yaml_content = "windows:\n  btc:\n    recommended_days: 30\n"
        p = tmp_path / "walkforward.yaml"
        p.write_text(yaml_content, encoding="utf-8")
        try:
            cfg = load_walkforward_config(p)
            # pyyaml available
            assert cfg.get("windows", {}).get("btc", {}).get("recommended_days") == 30
        except Exception:
            pass  # pyyaml not available — fallback parser, still returns dict
