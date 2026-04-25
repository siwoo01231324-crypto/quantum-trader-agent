"""Unit tests for PurgedKFold (src/ml/cv.py)."""
import numpy as np
import pandas as pd
import pytest

from ml.cv import PurgedKFold


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_X_t1(n: int = 100, label_lag: int = 5) -> tuple[pd.DataFrame, pd.Series]:
    """Return synthetic X (DatetimeIndex) and t1 (entry + label_lag bars)."""
    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    X = pd.DataFrame({"feat": np.random.default_rng(0).standard_normal(n)}, index=idx)
    t1 = pd.Series(idx[np.minimum(np.arange(n) + label_lag, n - 1)], index=idx)
    return X, t1


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_purge_no_train_test_overlap():
    """train ∩ test must be empty after purge."""
    X, t1 = _make_X_t1(200, label_lag=10)
    cv = PurgedKFold(n_splits=5, embargo_frac=0.01)
    for train_idx, test_idx in cv.split(X, t1):
        assert len(np.intersect1d(train_idx, test_idx)) == 0, (
            "train ∩ test ≠ ∅"
        )


def test_purge_removes_overlapping_train_samples():
    """Train samples whose t1 overlaps the test fold's entry range are removed."""
    n = 100
    label_lag = 20  # large lag to force overlap
    X, t1 = _make_X_t1(n, label_lag=label_lag)
    cv = PurgedKFold(n_splits=5, embargo_frac=0.0)
    for train_idx, test_idx in cv.split(X, t1):
        test_entry_min = X.index[test_idx[0]]
        # No train sample should have t1 >= test_entry_min AND entry <= test_entry_max
        test_entry_max = X.index[test_idx[-1]]
        for i in train_idx:
            entry_t = X.index[i]
            label_end = t1.iloc[i]
            overlap = label_end >= test_entry_min and entry_t <= test_entry_max
            assert not overlap, (
                f"Train sample {i} overlaps test fold but was not purged"
            )


def test_embargo_excludes_post_test_bars():
    """Embargo bars immediately following the test fold must not be in train."""
    n = 200
    X, t1 = _make_X_t1(n, label_lag=3)
    embargo_frac = 0.05
    cv = PurgedKFold(n_splits=4, embargo_frac=embargo_frac)
    embargo_n = int(np.ceil(n * embargo_frac))

    folds = list(cv.split(X, t1))
    for train_idx, test_idx in folds:
        test_end = test_idx[-1] + 1
        embargo_end = min(test_end + embargo_n, n)
        embargoed = set(range(test_end, embargo_end))
        assert embargoed.isdisjoint(set(train_idx.tolist())), (
            "Embargoed bars found in training set"
        )


def test_correct_number_of_folds():
    """split() yields exactly n_splits folds."""
    X, t1 = _make_X_t1(100, label_lag=2)
    for n_splits in [2, 3, 5]:
        cv = PurgedKFold(n_splits=n_splits)
        folds = list(cv.split(X, t1))
        assert len(folds) == n_splits


def test_raises_on_n_splits_less_than_2():
    with pytest.raises(ValueError, match="n_splits"):
        PurgedKFold(n_splits=1)


def test_raises_on_negative_embargo_frac():
    with pytest.raises(ValueError, match="embargo_frac"):
        PurgedKFold(embargo_frac=-0.01)
