"""Combinatorially Symmetric Cross-Validation (CSCV).

Bailey, Borwein, López de Prado, Zhu (2014). The Probability of
Backtest Overfitting. Journal of Computational Finance, 20(4), 39-69.

CSCV partitions a returns matrix of shape (T, N) into ``n_groups``
contiguous time-blocks and enumerates every (n_groups choose
n_groups//2) split between in-sample (IS) and out-of-sample (OOS).
For each split it picks the IS-best strategy and ranks its OOS
performance among the N candidates. The Probability of Backtest
Overfitting (PBO) is the fraction of splits in which the IS-best
strategy underperforms the OOS median (relative rank > 0.5).
"""
from __future__ import annotations

from itertools import combinations
import math

import numpy as np


def combinatorial_symmetric_cv(
    returns_matrix: "np.ndarray",
    n_groups: int = 16,
) -> dict:
    """Run CSCV on a returns matrix and compute PBO.

    Parameters
    ----------
    returns_matrix:
        Array of shape ``(T, N)`` where ``T`` is the number of OOS
        return observations (typically daily, concatenated chronologically
        across PurgedKFold test folds with no shuffling) and ``N`` is the
        number of candidate strategy variants. Days with no trades for a
        variant should be filled with 0.0.
    n_groups:
        Number of contiguous time blocks to split T into. Must be even
        so the symmetric IS/OOS split (n_groups//2 blocks each) is
        well-defined. Project SOP defaults to 16 (12-validation-protocol
        §3.5), giving C(16, 8) = 12_870 combinations.

    Returns
    -------
    dict
        ``{"pbo": float, "logits": np.ndarray, "n_combinations": int,
        "rank_correlations": np.ndarray}``

        - ``pbo``: probability of backtest overfitting in [0, 1].
        - ``logits``: per-combination logit of the relative OOS rank.
          Defined as ``log(λ / (1 - λ))`` where λ is the relative
          OOS rank of the IS-best strategy among N candidates.
        - ``n_combinations``: C(n_groups, n_groups // 2).
        - ``rank_correlations``: Spearman rank correlation between IS
          and OOS performance for each combination (length
          ``n_combinations``).

    Raises
    ------
    ValueError
        If ``returns_matrix`` is not 2-D, ``n_groups`` is not even, or
        ``T`` cannot be split into ``n_groups`` blocks of size >= 1.
    """
    arr = np.asarray(returns_matrix, dtype=float)
    if arr.ndim != 2:
        raise ValueError(
            f"returns_matrix must be 2-D (T, N), got shape {arr.shape}"
        )
    if n_groups < 2 or n_groups % 2 != 0:
        raise ValueError(
            f"n_groups must be an even integer >= 2, got {n_groups}"
        )

    t_total, n_strategies = arr.shape
    if n_strategies < 2:
        raise ValueError(
            f"need at least 2 strategy columns, got {n_strategies}"
        )
    if t_total < n_groups:
        raise ValueError(
            f"T={t_total} too small for n_groups={n_groups}"
        )

    # Split T into n_groups contiguous blocks
    block_bounds = np.linspace(0, t_total, n_groups + 1, dtype=int)
    blocks = [
        np.arange(block_bounds[i], block_bounds[i + 1])
        for i in range(n_groups)
    ]

    half = n_groups // 2
    group_ids = list(range(n_groups))
    n_combinations = math.comb(n_groups, half)

    logits = np.empty(n_combinations, dtype=float)
    rank_corrs = np.empty(n_combinations, dtype=float)
    overfit_count = 0

    for c_idx, is_groups in enumerate(combinations(group_ids, half)):
        is_set = set(is_groups)
        is_idx = np.concatenate([blocks[g] for g in group_ids if g in is_set])
        oos_idx = np.concatenate(
            [blocks[g] for g in group_ids if g not in is_set]
        )

        is_perf = arr[is_idx].mean(axis=0)  # shape (N,)
        oos_perf = arr[oos_idx].mean(axis=0)

        # IS-best variant
        best_idx = int(np.argmax(is_perf))

        # Relative OOS rank of IS-best among N (1 = best, N = worst).
        # Convert to relative rank in [1/N, 1] then to overfit lambda
        # (probability of being worse than median) per Bailey 2014.
        oos_ranks = _rankdata_descending(oos_perf)  # 1 = best
        rel_rank = float(oos_ranks[best_idx]) / float(n_strategies)
        # rel_rank close to 0 = good (IS-best won OOS too).
        # rel_rank close to 1 = bad (IS-best lost OOS).
        # Bailey's lambda = rel_rank itself (overfit probability).
        # PBO = fraction of combos where rel_rank > 0.5.
        if rel_rank > 0.5:
            overfit_count += 1

        # Logit of rel_rank, with clipping to avoid ±inf
        clipped = min(max(rel_rank, 1e-9), 1.0 - 1e-9)
        logits[c_idx] = math.log(clipped / (1.0 - clipped))

        # Spearman rank correlation of IS vs OOS perf
        rank_corrs[c_idx] = _spearman(is_perf, oos_perf)

    pbo = overfit_count / n_combinations
    return {
        "pbo": float(pbo),
        "logits": logits,
        "n_combinations": int(n_combinations),
        "rank_correlations": rank_corrs,
    }


def _rankdata_descending(arr: "np.ndarray") -> "np.ndarray":
    """Rank entries of ``arr`` so that the largest gets rank 1.

    Ties get the average rank, matching ``scipy.stats.rankdata`` with
    method='average' applied to ``-arr``.
    """
    a = np.asarray(arr, dtype=float)
    order = np.argsort(-a, kind="mergesort")
    ranks = np.empty_like(a, dtype=float)
    n = a.size

    i = 0
    while i < n:
        j = i
        # Group ties (within tiny tolerance for float equality)
        while j + 1 < n and a[order[j + 1]] == a[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # ranks are 1-indexed
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def _spearman(x: "np.ndarray", y: "np.ndarray") -> float:
    """Spearman rank correlation, returning 0.0 when degenerate."""
    rx = _rankdata_descending(x)
    ry = _rankdata_descending(y)
    rx_c = rx - rx.mean()
    ry_c = ry - ry.mean()
    denom = math.sqrt(float((rx_c ** 2).sum()) * float((ry_c ** 2).sum()))
    if denom == 0.0:
        return 0.0
    return float((rx_c * ry_c).sum() / denom)
