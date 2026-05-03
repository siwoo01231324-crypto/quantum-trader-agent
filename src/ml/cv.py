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


class TimeBlockGroupKFold:
    """Time-block CV that groups ALL symbols in a timestamp block into test fold.

    Unlike PurgedKFold (row-based fold boundaries), this splits on unique
    timestamps so that multi-symbol data is always split at time boundaries —
    no symbol leaks a future timestamp into the training set.

    Supports both single DatetimeIndex and MultiIndex(timestamp, symbol).

    Parameters
    ----------
    n_splits:
        Number of time-block folds (>= 2).
    embargo_frac:
        Fraction of unique timestamps to embargo after each test fold (>= 0).
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
        """Yield (train_indices, test_indices) split by time blocks.

        All rows sharing a timestamp in the test block go to the test fold.
        Purge and embargo are applied to the remaining rows.

        Parameters
        ----------
        X:
            Feature DataFrame. Index must be DatetimeIndex or
            MultiIndex with DatetimeIndex as level 0.
        t1:
            Label-completion timestamps indexed identically to X.

        Yields
        ------
        tuple[np.ndarray, np.ndarray]
            (train_idx, test_idx) integer position arrays.
        """
        if isinstance(X.index, pd.MultiIndex):
            timestamps = X.index.get_level_values(0)
        else:
            timestamps = X.index

        if not pd.api.types.is_datetime64_any_dtype(timestamps):
            raise ValueError("X must have a DatetimeIndex (or MultiIndex level-0 datetime)")

        unique_times = timestamps.unique().sort_values()
        n_times = len(unique_times)
        block_size = n_times // self.n_splits
        embargo_n = int(np.ceil(n_times * self.embargo_frac))

        n = len(X)
        indices = np.arange(n)
        ts_array = timestamps.to_numpy()

        for k in range(self.n_splits):
            t_start = k * block_size
            t_end = (t_start + block_size) if k < self.n_splits - 1 else n_times
            test_times = set(unique_times[t_start:t_end])

            test_entry_min = unique_times[t_start]
            test_entry_max = unique_times[t_end - 1]

            test_mask = np.array([ts_array[i] in test_times for i in range(n)])
            test_idx = indices[test_mask]

            # Embargo: exclude unique_times immediately after test block
            embargo_end_idx = min(t_end + embargo_n, n_times)
            embargo_times = set(unique_times[t_end:embargo_end_idx])

            train_mask = ~test_mask
            for i in indices:
                if not train_mask[i]:
                    continue
                ts_i = ts_array[i]
                # Purge: label window overlaps test fold
                if t1.iloc[i] >= test_entry_min and ts_i <= test_entry_max:
                    train_mask[i] = False
                    continue
                # Embargo
                if ts_i in embargo_times:
                    train_mask[i] = False

            train_idx = indices[train_mask]

            assert len(np.intersect1d(train_idx, test_idx)) == 0, (
                "TimeBlockGroupKFold invariant violated: train ∩ test ≠ ∅"
            )

            yield train_idx, test_idx
