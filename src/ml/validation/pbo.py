"""Probability of Backtest Overfitting (PBO).

Convenience wrapper around the CSCV machinery. Project gate: PBO <= 0.2.

References
----------
Bailey, Borwein, López de Prado, Zhu (2014). The Probability of
Backtest Overfitting. Journal of Computational Finance, 20(4), 39-69.
"""
from __future__ import annotations

import numpy as np

from src.ml.validation.cscv import combinatorial_symmetric_cv


def probability_of_backtest_overfitting(
    returns_matrix: "np.ndarray",
    n_groups: int = 16,
) -> float:
    """Compute PBO via CSCV.

    Parameters
    ----------
    returns_matrix:
        Array of shape ``(T, N)`` (see ``combinatorial_symmetric_cv``).
    n_groups:
        Number of CSCV blocks (must be even). Default 16 per project SOP.

    Returns
    -------
    float
        PBO in [0, 1]. PBO <= 0.2 passes the project gate
        (12-validation-protocol §3.7).
    """
    result = combinatorial_symmetric_cv(returns_matrix, n_groups=n_groups)
    return result["pbo"]
