"""Tests for TimeBlockGroupKFold in src/ml/cv.py."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.ml.cv import PurgedKFold, TimeBlockGroupKFold


def _make_multi_symbol_data(
    n_symbols: int = 3,
    n_timestamps: int = 100,
    seed: int = 0,
) -> tuple[pd.DataFrame, pd.Series]:
    """Build synthetic multi-symbol DataFrame with MultiIndex(ts, symbol)."""
    rng = np.random.default_rng(seed)
    timestamps = pd.date_range("2023-01-01", periods=n_timestamps, freq="1D", tz="UTC")
    symbols = [f"S{i}" for i in range(n_symbols)]

    rows = []
    for ts in timestamps:
        for sym in symbols:
            rows.append({"ts": ts, "symbol": sym, "feat": rng.standard_normal()})

    df = pd.DataFrame(rows).set_index(["ts", "symbol"])
    # t1: label ends 5 days after entry
    t1 = pd.Series(
        [row["ts"] + pd.Timedelta(days=5) for row in rows],
        index=df.index,
    )
    return df, t1


def _make_single_index_data(n: int = 100) -> tuple[pd.DataFrame, pd.Series]:
    idx = pd.date_range("2023-01-01", periods=n, freq="1D", tz="UTC")
    df = pd.DataFrame({"feat": np.random.randn(n)}, index=idx)
    t1 = pd.Series(idx + pd.Timedelta(days=3), index=idx)
    return df, t1


class TestTimeBlockGroupKFold:
    def test_fold_count_default(self):
        df, t1 = _make_multi_symbol_data()
        cv = TimeBlockGroupKFold(n_splits=5)
        folds = list(cv.split(df, t1))
        assert len(folds) == 5

    def test_test_blocks_cover_all_symbols(self):
        """Each test fold must include all symbols for those timestamps."""
        n_symbols = 3
        df, t1 = _make_multi_symbol_data(n_symbols=n_symbols, n_timestamps=60)
        cv = TimeBlockGroupKFold(n_splits=5)
        timestamps = df.index.get_level_values(0)
        for train_idx, test_idx in cv.split(df, t1):
            test_ts = set(timestamps[test_idx])
            # For each test timestamp, all 3 symbols' rows must be in test
            for ts in test_ts:
                rows_for_ts = np.where(timestamps == ts)[0]
                for r in rows_for_ts:
                    assert r in test_idx, f"Row {r} (ts={ts}) missing from test fold"

    def test_train_test_time_no_overlap(self):
        """Train and test time ranges must not overlap."""
        df, t1 = _make_multi_symbol_data(n_timestamps=50)
        cv = TimeBlockGroupKFold(n_splits=5)
        timestamps = df.index.get_level_values(0)
        for train_idx, test_idx in cv.split(df, t1):
            train_ts = set(timestamps[train_idx])
            test_ts = set(timestamps[test_idx])
            overlap = train_ts & test_ts
            assert len(overlap) == 0, f"Time overlap between train and test: {overlap}"

    def test_no_index_overlap(self):
        """Integer position sets must be disjoint."""
        df, t1 = _make_multi_symbol_data()
        cv = TimeBlockGroupKFold(n_splits=5)
        for train_idx, test_idx in cv.split(df, t1):
            assert len(np.intersect1d(train_idx, test_idx)) == 0

    def test_same_signature_as_purged_kfold(self):
        """TimeBlockGroupKFold.split must accept same args as PurgedKFold.split."""
        df_single, t1_single = _make_single_index_data(n=100)
        pkf = PurgedKFold(n_splits=5)
        tbgkf = TimeBlockGroupKFold(n_splits=5)
        pkf_folds = list(pkf.split(df_single, t1_single))
        tbgkf_folds = list(tbgkf.split(df_single, t1_single))
        assert len(pkf_folds) == len(tbgkf_folds) == 5

    def test_single_datetime_index(self):
        """Works with a plain DatetimeIndex (non-MultiIndex)."""
        df, t1 = _make_single_index_data(n=60)
        cv = TimeBlockGroupKFold(n_splits=4)
        folds = list(cv.split(df, t1))
        assert len(folds) == 4
        for train_idx, test_idx in folds:
            assert len(np.intersect1d(train_idx, test_idx)) == 0

    def test_embargo_excludes_blocks_after_test(self):
        """With large embargo, the timestamps immediately after test fold are excluded."""
        df, t1 = _make_multi_symbol_data(n_timestamps=30, n_symbols=2)
        cv = TimeBlockGroupKFold(n_splits=3, embargo_frac=0.1)
        timestamps = df.index.get_level_values(0)
        unique_times = sorted(timestamps.unique())
        block_size = len(unique_times) // 3

        for k, (train_idx, test_idx) in enumerate(cv.split(df, t1)):
            t_end = (k + 1) * block_size if k < 2 else len(unique_times)
            embargo_n = int(np.ceil(len(unique_times) * 0.1))
            embargo_end = min(t_end + embargo_n, len(unique_times))
            embargo_times = set(unique_times[t_end:embargo_end])
            train_ts = set(timestamps[train_idx])
            for ets in embargo_times:
                assert ets not in train_ts, f"Embargo time {ets} found in train"

    def test_purged_kfold_unchanged(self):
        """PurgedKFold still works correctly after cv.py was modified."""
        df, t1 = _make_single_index_data(n=50)
        pkf = PurgedKFold(n_splits=5)
        folds = list(pkf.split(df, t1))
        assert len(folds) == 5
        for train_idx, test_idx in folds:
            assert len(np.intersect1d(train_idx, test_idx)) == 0

    def test_n_splits_validation(self):
        with pytest.raises(ValueError, match="n_splits"):
            TimeBlockGroupKFold(n_splits=1)

    def test_embargo_frac_validation(self):
        with pytest.raises(ValueError, match="embargo_frac"):
            TimeBlockGroupKFold(embargo_frac=-0.1)
