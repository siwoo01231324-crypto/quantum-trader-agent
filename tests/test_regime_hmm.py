"""Tests for HMM regime detection module.

TDD: synthetic 2-state data -> HMM should recover the two regimes.
Synthetic data: state 0 (low vol): mu=+0.001, sigma=0.005
               state 1 (high vol): mu=-0.001, sigma=0.02
"""
from __future__ import annotations

import numpy as np
import pytest

from src.ml.regime.hmm import GaussianHMMRegime, RegimeResult


def _make_synthetic_2state(
    n_per_state: int = 500,
    mu_low: float = 0.001,
    sigma_low: float = 0.005,
    mu_high: float = -0.001,
    sigma_high: float = 0.02,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate synthetic returns with two alternating regimes."""
    rng = np.random.default_rng(seed)
    block_size = n_per_state
    returns = []
    true_states = []
    for block in range(4):
        if block % 2 == 0:
            r = rng.normal(mu_low, sigma_low, block_size)
            s = np.zeros(block_size, dtype=int)
        else:
            r = rng.normal(mu_high, sigma_high, block_size)
            s = np.ones(block_size, dtype=int)
        returns.append(r)
        true_states.append(s)
    return np.concatenate(returns), np.concatenate(true_states)


class TestGaussianHMMRegime:
    def test_fit_predict_2state_recovers_regimes(self):
        returns, true_states = _make_synthetic_2state()
        model = GaussianHMMRegime(n_components=2, random_state=42)
        result = model.fit_predict(returns)

        assert isinstance(result, RegimeResult)
        assert result.n_components == 2
        assert len(result.states) == len(returns)
        assert result.means.shape == (2,)
        assert result.variances.shape == (2,)
        assert result.transmat.shape == (2, 2)

        low_vol_state = int(np.argmin(result.variances))
        high_vol_state = 1 - low_vol_state

        assert result.variances[low_vol_state] < result.variances[high_vol_state]
        assert result.means[low_vol_state] > result.means[high_vol_state]

    def test_state_recovery_accuracy_above_80pct(self):
        returns, true_states = _make_synthetic_2state(n_per_state=500)
        model = GaussianHMMRegime(n_components=2, random_state=42)
        result = model.fit_predict(returns)

        low_vol_hmm = int(np.argmin(result.variances))

        pred_remapped = np.where(result.states == low_vol_hmm, 0, 1)

        acc_direct = np.mean(pred_remapped == true_states)
        acc_flip = np.mean((1 - pred_remapped) == true_states)
        accuracy = max(acc_direct, acc_flip)

        assert accuracy > 0.80, f"Accuracy {accuracy:.2%} below 80% threshold"

    def test_transition_matrix_rows_sum_to_1(self):
        returns, _ = _make_synthetic_2state()
        model = GaussianHMMRegime(n_components=2, random_state=42)
        result = model.fit_predict(returns)

        row_sums = result.transmat.sum(axis=1)
        np.testing.assert_allclose(row_sums, 1.0, atol=1e-6)

    def test_state_persistence_high_for_clean_data(self):
        returns, _ = _make_synthetic_2state(n_per_state=1000)
        model = GaussianHMMRegime(n_components=2, random_state=42)
        result = model.fit_predict(returns)

        persistence = result.state_persistence
        assert np.all(persistence > 0.90), (
            f"State persistence {persistence} should be > 0.90 for clean block data"
        )

    def test_state_label_low_vol_high_vol(self):
        returns, _ = _make_synthetic_2state()
        model = GaussianHMMRegime(n_components=2, random_state=42)
        result = model.fit_predict(returns)

        labels = {result.state_label(i) for i in range(2)}
        assert labels == {"low_vol", "high_vol"}

    def test_3state_hmm(self):
        rng = np.random.default_rng(123)
        s0 = rng.normal(0.002, 0.003, 300)
        s1 = rng.normal(0.0, 0.01, 300)
        s2 = rng.normal(-0.002, 0.025, 300)
        returns = np.concatenate([s0, s1, s2, s0, s1, s2])

        model = GaussianHMMRegime(n_components=3, random_state=42)
        result = model.fit_predict(returns)

        assert result.n_components == 3
        assert len(result.means) == 3
        assert result.transmat.shape == (3, 3)

        labels = {result.state_label(i) for i in range(3)}
        assert labels == {"low_vol", "mid_vol", "high_vol"}

    def test_fit_predict_with_pandas_series(self):
        import pandas as pd

        returns, _ = _make_synthetic_2state(n_per_state=200)
        idx = pd.date_range("2020-01-01", periods=len(returns), freq="4h")
        series = pd.Series(returns, index=idx)

        model = GaussianHMMRegime(n_components=2, random_state=42)
        result = model.fit_predict(series)

        assert len(result.states) == len(returns)

    def test_predict_before_fit_raises(self):
        model = GaussianHMMRegime(n_components=2)
        with pytest.raises(RuntimeError, match="Call fit"):
            model.predict(np.array([0.01, -0.01, 0.02]))

    def test_too_few_observations_raises(self):
        model = GaussianHMMRegime(n_components=2)
        with pytest.raises(ValueError, match="at least"):
            model.fit(np.array([0.01, 0.02]))

    def test_n_components_below_2_raises(self):
        with pytest.raises(ValueError, match="n_components must be >= 2"):
            GaussianHMMRegime(n_components=1)

    def test_nan_handling(self):
        returns, _ = _make_synthetic_2state(n_per_state=200)
        returns_with_nan = np.copy(returns)
        returns_with_nan[0] = np.nan
        returns_with_nan[100] = np.nan

        model = GaussianHMMRegime(n_components=2, random_state=42)
        result = model.fit_predict(returns_with_nan)

        assert len(result.states) == len(returns) - 2

    def test_score_is_finite(self):
        returns, _ = _make_synthetic_2state()
        model = GaussianHMMRegime(n_components=2, random_state=42)
        result = model.fit_predict(returns)

        assert np.isfinite(result.score)
