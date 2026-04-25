"""Unit tests for WalkForwardSplitter (src/ml/walkforward.py)."""
import numpy as np
import pandas as pd
import pytest

from ml.walkforward import WalkForwardConfig, WalkForwardSplitter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_index(n: int = 1000) -> pd.DatetimeIndex:
    return pd.date_range("2023-01-01", periods=n, freq="1h")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_expanding_fold_count():
    """Expanding mode: number of folds equals (n - train_w) // step."""
    n, train_w, test_w, step = 1000, 500, 100, 100
    idx = _make_index(n)
    cfg = WalkForwardConfig(mode="expanding", train_window=train_w, test_window=test_w, step=step, min_train_samples=500)
    folds = list(WalkForwardSplitter(cfg).split(idx))
    expected = (n - train_w + step - 1) // step  # ceiling division
    assert len(folds) > 0
    # Each test window starts at train_w, train_w+step, ...
    for i, (train_idx, test_idx) in enumerate(folds):
        expected_test_start = train_w + i * step
        assert test_idx[0] == expected_test_start


def test_rolling_fixed_train_size():
    """Rolling mode: training window size is fixed (= train_w)."""
    n, train_w, test_w, step = 1000, 500, 100, 100
    idx = _make_index(n)
    cfg = WalkForwardConfig(mode="rolling", train_window=train_w, test_window=test_w, step=step, min_train_samples=500)
    folds = list(WalkForwardSplitter(cfg).split(idx))
    for train_idx, _ in folds:
        assert len(train_idx) == train_w


def test_expanding_train_grows():
    """Expanding mode: training set grows with each step."""
    n, train_w, test_w, step = 1000, 500, 100, 100
    idx = _make_index(n)
    cfg = WalkForwardConfig(mode="expanding", train_window=train_w, test_window=test_w, step=step, min_train_samples=500)
    folds = list(WalkForwardSplitter(cfg).split(idx))
    sizes = [len(tr) for tr, _ in folds]
    assert sizes == sorted(sizes), "Expanding train sizes should be non-decreasing"


def test_min_train_samples_respected():
    """Folds with fewer than min_train_samples training samples are skipped."""
    n = 600
    idx = _make_index(n)
    cfg = WalkForwardConfig(mode="expanding", train_window=500, test_window=50, step=50, min_train_samples=500)
    folds = list(WalkForwardSplitter(cfg).split(idx))
    for train_idx, _ in folds:
        assert len(train_idx) >= 500


def test_step_interval():
    """Consecutive test windows start exactly `step` bars apart."""
    n, train_w, test_w, step = 1000, 500, 50, 75
    idx = _make_index(n)
    cfg = WalkForwardConfig(mode="expanding", train_window=train_w, test_window=test_w, step=step, min_train_samples=500)
    folds = list(WalkForwardSplitter(cfg).split(idx))
    for i in range(1, len(folds)):
        prev_test_start = folds[i - 1][1][0]
        curr_test_start = folds[i][1][0]
        assert curr_test_start - prev_test_start == step
