"""Factor exposure neutralization — OLS and Gram-Schmidt orthogonal methods.

Patent reference: US20140081889A1 (Axioma — factor-exposure-based portfolio construction) — abandoned.
US20140081889A1 abandoned. We cite it here for transparency; the abandoned status means no
infringement risk. Our implementation is a standalone residualization utility, structurally
distinct from Axioma's optimization-engine claims. "US20140081889A1" + "abandoned".

Single entry point: neutralize(raw, *exposures, method="ols")
"""
from __future__ import annotations

import logging
import warnings
from typing import Literal

import numpy as np

logger = logging.getLogger(__name__)

_COND_WARN_THRESHOLD = 1e8
_COND_FALLBACK_THRESHOLD = 1e10


def neutralize(
    raw: np.ndarray,
    *exposures: np.ndarray,
    method: Literal["ols", "orthogonal"] = "ols",
) -> np.ndarray:
    """Remove factor exposure(s) from a raw signal vector.

    Parameters
    ----------
    raw:
        1-D array of raw signal values (length T).
    *exposures:
        One or more 1-D exposure arrays, each of length T.
    method:
        "ols" — project raw onto exposure space via OLS, return residual.
        "orthogonal" — Gram-Schmidt orthogonalization of exposures, then residualize.
        Both methods produce a residual orthogonal to all exposures (within float64 precision).

    Numerical guard (two-tier):
        cond(stacked_exposures) > 1e8  → warnings.warn (grey zone, still usable)
        cond(stacked_exposures) > 1e10 → deterministic fallback to OLS path

    Returns
    -------
    np.ndarray
        Residual vector of length T, orthogonal to all provided exposures.
    """
    raw = np.asarray(raw, dtype=float)
    if len(exposures) == 0:
        return raw.copy()

    X = np.column_stack([np.asarray(e, dtype=float) for e in exposures])  # shape (T, k)

    if method == "orthogonal":
        cond = np.linalg.cond(X)
        if cond > _COND_FALLBACK_THRESHOLD:
            logger.warning("neutralize: cond=%.3e, fallback=ols (threshold=%.0e)", cond, _COND_FALLBACK_THRESHOLD)
            method = "ols"
        elif cond > _COND_WARN_THRESHOLD:
            warnings.warn(
                f"neutralize: cond={cond:.2e} approaching grey zone for float64",
                RuntimeWarning,
                stacklevel=2,
            )

    if method == "ols":
        return _ols_residual(raw, X)
    else:
        return _orthogonal_residual(raw, X)


def _ols_residual(raw: np.ndarray, X: np.ndarray) -> np.ndarray:
    """OLS projection residual: raw - X @ pinv(X) @ raw."""
    coeffs, _, _, _ = np.linalg.lstsq(X, raw, rcond=None)
    return raw - X @ coeffs


def _orthogonal_residual(raw: np.ndarray, X: np.ndarray) -> np.ndarray:
    """Gram-Schmidt orthogonalize columns of X, then residualize raw."""
    T, k = X.shape
    Q = np.zeros_like(X)
    for i in range(k):
        v = X[:, i].copy()
        for j in range(i):
            v -= np.dot(Q[:, j], v) * Q[:, j]
        norm = np.linalg.norm(v)
        if norm < 1e-12:
            continue
        Q[:, i] = v / norm

    # Project raw onto each orthonormal basis vector and subtract
    residual = raw.copy()
    for i in range(k):
        q = Q[:, i]
        if np.linalg.norm(q) < 1e-12:
            continue
        residual -= np.dot(q, residual) * q
    return residual
