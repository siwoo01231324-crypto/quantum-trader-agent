"""Purged K-fold + embargo CV — López de Prado AFML Ch.7."""
from __future__ import annotations

from typing import Iterator
import numpy as np
import pandas as pd


class PurgedKFold:
    """Time-series cross-validation that prevents lookahead leakage.

    Purge: any training sample whose label window (entry_ts → t1) overlaps
    with the test fold's entry-time range is removed from training.

    Embargo: samples immediately following the test fold (up to
    ``embargo_frac * N`` bars) are also excluded from training to prevent
    leakage through autocorrelated features.

    Parameters
    ----------
    n_splits:
        Number of folds (>= 2).
    embargo_frac:
        Fraction of total samples to embargo after each test fold (>= 0).
    """

    def __init__(self, n_splits: int = 5, embargo_frac: float = 0.01) -> None:
        if n_splits < 2:
            raise ValueError(f"n_splits must be >= 2, got {n_splits}")
        if embargo_frac < 0:
            raise ValueError(f"embargo_frac must be >= 0, got {embargo_frac}")
        self.n_splits = n_splits
        self.embargo_frac = embargo_frac

    def split(
        self,
        X: pd.DataFrame,
        t1: pd.Series,
    ) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        """Yield (train_indices, test_indices) with purge + embargo applied.

        Parameters
        ----------
        X:
            Feature DataFrame with DatetimeIndex.
        t1:
            Label-completion timestamps (t_touch from triple_barrier_label),
            indexed identically to X.

        Yields
        ------
        tuple[np.ndarray, np.ndarray]
            (train_idx, test_idx) integer position arrays.

        Raises
        ------
        ValueError
            If X does not have a DatetimeIndex or indices do not match.
        """
        if not isinstance(X.index, pd.DatetimeIndex):
            raise ValueError("X must have a DatetimeIndex")
        if not X.index.equals(t1.index):
            raise ValueError("X.index and t1.index must be identical")

        n = len(X)
        embargo_n = int(np.ceil(n * self.embargo_frac))
        indices = np.arange(n)

        fold_size = n // self.n_splits
        fold_bounds: list[tuple[int, int]] = []
        for k in range(self.n_splits):
            start = k * fold_size
            end = (start + fold_size) if k < self.n_splits - 1 else n
            fold_bounds.append((start, end))

        for test_start, test_end in fold_bounds:
            test_idx = indices[test_start:test_end]

            # Entry times of the test fold
            test_entry_min = X.index[test_start]
            test_entry_max = X.index[test_end - 1]

            # Build train mask: start with everything outside test fold
            train_mask = np.ones(n, dtype=bool)
            train_mask[test_start:test_end] = False

            # Purge: remove samples whose label window overlaps the test fold.
            # Overlap condition: label ends at or after test start AND
            # sample entry is at or before test end.
            for i in indices:
                if train_mask[i]:
                    if t1.iloc[i] >= test_entry_min and X.index[i] <= test_entry_max:
                        train_mask[i] = False

            # Embargo: exclude bars immediately after the test fold
            embargo_end = min(test_end + embargo_n, n)
            train_mask[test_end:embargo_end] = False

            train_idx = indices[train_mask]

            # Invariant: no overlap between train and test index sets
            assert len(np.intersect1d(train_idx, test_idx)) == 0, (
                "PurgedKFold invariant violated: train ∩ test ≠ ∅"
            )

            yield train_idx, test_idx
