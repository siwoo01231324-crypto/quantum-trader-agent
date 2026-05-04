"""Hamilton (1989) 2/3-state Gaussian HMM for regime detection.

Uses hmmlearn's GaussianHMM with EM (Baum-Welch) for parameter estimation
and Viterbi decoding for state sequence inference.

References:
    Hamilton, J.D. (1989). Econometrica 57(2), 357-384.
    Ang, A. & Bekaert, G. (2002). RFS 15(4), 1137-1187.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM


@dataclass(frozen=True)
class RegimeResult:
    """Container for HMM regime detection output."""

    states: np.ndarray
    means: np.ndarray
    variances: np.ndarray
    transmat: np.ndarray
    score: float
    n_components: int
    converged: bool

    @property
    def state_persistence(self) -> np.ndarray:
        return np.diag(self.transmat)

    def state_label(self, state_id: int) -> str:
        vol = float(np.sqrt(self.variances[state_id]))
        mu = float(self.means[state_id])
        if self.n_components == 2:
            low_vol_state = int(np.argmin(self.variances))
            return "low_vol" if state_id == low_vol_state else "high_vol"
        sorted_by_vol = np.argsort(self.variances.flatten())
        rank = int(np.where(sorted_by_vol == state_id)[0][0])
        labels = ["low_vol", "mid_vol", "high_vol"]
        return labels[rank] if rank < len(labels) else f"state_{state_id}"


class GaussianHMMRegime:
    """Gaussian HMM regime detector.

    Parameters
    ----------
    n_components : Number of hidden states (2 or 3).
    n_iter : Maximum EM iterations.
    random_state : Seed for reproducibility.
    covariance_type : 'full', 'diag', 'spherical', or 'tied'.
    """

    def __init__(
        self,
        n_components: int = 2,
        n_iter: int = 100,
        random_state: int = 42,
        covariance_type: str = "full",
    ) -> None:
        if n_components < 2:
            raise ValueError("n_components must be >= 2")
        self.n_components = n_components
        self._model = GaussianHMM(
            n_components=n_components,
            covariance_type=covariance_type,
            n_iter=n_iter,
            random_state=random_state,
        )
        self._fitted = False

    def fit(self, returns: np.ndarray | pd.Series) -> GaussianHMMRegime:
        """Fit HMM on return series via Baum-Welch (EM)."""
        X = self._to_2d(returns)
        if len(X) < 2 * self.n_components:
            raise ValueError(
                f"Need at least {2 * self.n_components} observations, got {len(X)}"
            )
        self._model.fit(X)
        self._fitted = True
        return self

    def predict(self, returns: np.ndarray | pd.Series) -> RegimeResult:
        """Viterbi-decode state sequence and return structured result."""
        if not self._fitted:
            raise RuntimeError("Call fit() before predict()")
        X = self._to_2d(returns)
        states = self._model.predict(X)
        score = float(self._model.score(X))

        means = self._model.means_.flatten()
        if self._model.covariance_type == "full":
            variances = np.array([self._model.covars_[i][0, 0] for i in range(self.n_components)])
        elif self._model.covariance_type == "diag":
            variances = self._model.covars_.flatten()
        elif self._model.covariance_type == "spherical":
            variances = self._model.covars_.flatten()
        else:
            variances = np.array([self._model.covars_[0, 0]] * self.n_components)

        return RegimeResult(
            states=states,
            means=means,
            variances=variances,
            transmat=self._model.transmat_.copy(),
            score=score,
            n_components=self.n_components,
            converged=self._model.monitor_.converged,
        )

    def fit_predict(self, returns: np.ndarray | pd.Series) -> RegimeResult:
        """Fit and predict in one call."""
        self.fit(returns)
        return self.predict(returns)

    @staticmethod
    def _to_2d(data: np.ndarray | pd.Series) -> np.ndarray:
        if isinstance(data, pd.Series):
            arr = data.dropna().to_numpy()
        else:
            arr = np.asarray(data)
            arr = arr[~np.isnan(arr)]
        return arr.reshape(-1, 1)
