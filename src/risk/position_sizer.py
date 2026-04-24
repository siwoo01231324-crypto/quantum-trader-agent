# Patent avoidance:
#   ERC: Goldman Sachs 포기 특허 US20140081888A1 의 볼록 근사 접근법 학술 참고.
#        포기 특허이므로 법적 리스크 없음.
#   HRP 2단계 클러스터링: IBM 특허 US11562281B2 의 클러스터 분해 아이디어를
#        양자 컴포넌트 제외하고 차용. 고전 HRP 만 구현.
#        Marcos Lopez de Prado (2016) 원논문 재구현.
# cvxpy 미사용 — scipy SLSQP + scipy.cluster.hierarchy 만 사용.
"""Portfolio position sizing: ERC (convex) and cluster-HRP."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.optimize import minimize
from scipy.spatial.distance import squareform


def equal_risk_contribution_convex(
    cov: np.ndarray,
    target_contrib: np.ndarray | None = None,
    max_iter: int = 300,
) -> np.ndarray:
    """Convex ERC: min Σ_i (w_i·(Σw)_i - target)².

    Constraints: sum(w)=1, 0 <= w_i <= 1.
    Initial guess: 1/N uniform.
    On convergence failure: falls back to IVP (inverse-variance portfolio).

    Args:
        cov: (N, N) covariance matrix (positive semi-definite).
        target_contrib: (N,) target risk contributions. Defaults to equal 1/N.
        max_iter: Maximum SLSQP iterations.

    Returns:
        (N,) weight array with sum ≈ 1.0 and w_i ≥ 0.
    """
    n = cov.shape[0]
    if target_contrib is None:
        target_contrib = np.full(n, 1.0 / n)

    x0 = np.full(n, 1.0 / n)
    bounds = [(0.0, 1.0)] * n
    constraints = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}

    def objective(w: np.ndarray) -> float:
        # Σ_i (w_i*(Σw)_i - target_i * w'Σw)^2  — scale-invariant ERC objective
        portfolio_var = float(w @ cov @ w)
        risk = w * (cov @ w)
        return float(np.sum((risk - target_contrib * portfolio_var) ** 2))

    result = minimize(
        objective,
        x0,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": max_iter, "ftol": 1e-12},
    )

    if result.success:
        w = np.clip(result.x, 0.0, 1.0)
        w /= w.sum()
        assert abs(w.sum() - 1.0) < 1e-6
        return w

    # IVP fallback: weights proportional to 1/variance
    diag = np.diag(cov)
    diag = np.where(diag > 0, diag, 1e-10)
    w = 1.0 / diag
    w /= w.sum()
    assert abs(w.sum() - 1.0) < 1e-6
    return w


# ---------------------------------------------------------------------------
# HRP helpers
# ---------------------------------------------------------------------------

def _corr_to_dist(corr: np.ndarray) -> np.ndarray:
    """Convert correlation matrix to distance matrix d_ij = sqrt(0.5*(1-rho_ij))."""
    dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, 1.0))
    np.fill_diagonal(dist, 0.0)
    return dist


def _hrp_bisect(cov: np.ndarray, sort_idx: np.ndarray) -> np.ndarray:
    """Recursive bisection HRP on cov using the given asset ordering."""
    n = len(sort_idx)
    weights = np.ones(n)

    def _recurse(items: list[int]) -> None:
        if len(items) <= 1:
            return
        mid = len(items) // 2
        left, right = items[:mid], items[mid:]

        var_l = _cluster_var(cov, sort_idx[left])
        var_r = _cluster_var(cov, sort_idx[right])
        total = var_l + var_r
        if total == 0:
            alpha = 0.5
        else:
            alpha = 1.0 - var_l / total

        weights[left] *= 1.0 - alpha
        weights[right] *= alpha
        _recurse(left)
        _recurse(right)

    _recurse(list(range(n)))
    return weights


def _cluster_var(cov: np.ndarray, idx: np.ndarray) -> float:
    """Inverse-variance cluster variance for assets at idx."""
    sub = cov[np.ix_(idx, idx)]
    diag = np.diag(sub)
    diag = np.where(diag > 0, diag, 1e-10)
    ivp = 1.0 / diag
    ivp /= ivp.sum()
    return float(ivp @ sub @ ivp)


def _single_hrp(returns: pd.DataFrame) -> np.ndarray:
    """Classic single-pass HRP (Lopez de Prado 2016)."""
    corr = returns.corr().values
    dist = _corr_to_dist(corr)
    condensed = squareform(dist, checks=False)
    Z = linkage(condensed, method="single")
    # Quasi-diagonal ordering via dendrogram leaf order
    from scipy.cluster.hierarchy import leaves_list
    sort_idx = leaves_list(Z)
    cov = returns.cov().values
    w = _hrp_bisect(cov, sort_idx)
    w /= w.sum()
    return w


def hrp_with_clustering(
    returns: pd.DataFrame,
    k_clusters: int | None = None,
    linkage_method: str = "single",
) -> np.ndarray:
    """2-stage cluster-HRP (no quantum components).

    k_clusters=None or N<50: single HRP fallback.
    Otherwise:
      1. scipy.cluster.hierarchy.linkage → k cluster decomposition.
      2. Intra-cluster IVP weights.
      3. Inter-cluster HRP recursive bisection to combine.

    Only scipy.cluster.hierarchy used. No quantum/external clustering libs.

    Args:
        returns: (T, N) DataFrame of asset returns.
        k_clusters: Number of clusters. None → single HRP fallback.
        linkage_method: Linkage method passed to scipy linkage().

    Returns:
        (N,) weight array with sum ≈ 1.0 and w_i ≥ 0.
    """
    n = returns.shape[1]

    # Fallback conditions: k_clusters not specified or N too small
    if k_clusters is None or n < 50:
        w = _single_hrp(returns)
        assert abs(w.sum() - 1.0) < 1e-9
        return w

    # --- Stage 1: cluster assets ---
    corr = returns.corr().values
    dist = _corr_to_dist(corr)
    condensed = squareform(dist, checks=False)
    Z = linkage(condensed, method=linkage_method)
    labels = fcluster(Z, t=k_clusters, criterion="maxclust")  # 1-indexed

    # --- Stage 2: intra-cluster IVP ---
    cov = returns.cov().values
    cluster_weights = np.zeros(n)

    unique_clusters = np.unique(labels)
    cluster_var = np.zeros(len(unique_clusters))
    cluster_ivp = {}

    for ci, c in enumerate(unique_clusters):
        idx = np.where(labels == c)[0]
        sub_cov = cov[np.ix_(idx, idx)]
        diag = np.diag(sub_cov)
        diag = np.where(diag > 0, diag, 1e-10)
        ivp = 1.0 / diag
        ivp /= ivp.sum()
        cluster_ivp[c] = (idx, ivp)
        cluster_var[ci] = float(ivp @ sub_cov @ ivp)

    # --- Stage 3: inter-cluster HRP bisection ---
    # Build a pseudo-returns DataFrame with one "representative" series per cluster
    # (variance-weighted centroid) for inter-cluster HRP
    cluster_series = {}
    for c, (idx, ivp) in cluster_ivp.items():
        cluster_series[c] = (returns.iloc[:, idx].values @ ivp)

    cluster_df = pd.DataFrame(cluster_series)
    inter_w = _single_hrp(cluster_df)  # shape (k_clusters,)

    # Distribute inter-cluster weights into asset-level weights
    for ci, c in enumerate(unique_clusters):
        idx, ivp = cluster_ivp[c]
        cluster_weights[idx] = inter_w[ci] * ivp

    cluster_weights /= cluster_weights.sum()
    assert abs(cluster_weights.sum() - 1.0) < 1e-9
    return cluster_weights
