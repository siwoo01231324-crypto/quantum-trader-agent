"""Portfolio-level risk metrics (LW shrinkage Σ / Historical CVaR / Meucci ENB / avg ρ).

Theory and default-value citations live in docs/background/19-portfolio-risk.md.
Not invokable from LLM tool surface (CLAUDE.md 불변식 #6).
"""
from __future__ import annotations

import math
import warnings
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from sklearn.covariance import LedoitWolf

if TYPE_CHECKING:
    import pandas as pd


class ShortSampleWarning(UserWarning):
    """T (obs) < max(30, 2N) 일 때 발생. LW 추정 신뢰도 경고."""


class PortfolioRiskReport(BaseModel):
    """주기 평가기가 계산해 Snapshot.portfolio_risk 로 주입하는 불변 스냅샷.

    enb_ratio 가 NaN 인 경우 (degenerate Σ) 는 wrapper 에서 0.0 으로 치환 후
    evaluator 가 min_enb_ratio 체크 시 위반으로 간주한다.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    cvar_pct: float = Field(..., ge=0.0, description="Historical CVaR at alpha, positive loss fraction")
    var_pct: float = Field(..., ge=0.0, description="Historical VaR at alpha, positive loss fraction")
    corr_avg: float = Field(..., ge=-1.0, le=1.0)
    enb: float = Field(..., ge=0.0)
    enb_ratio: float = Field(..., ge=0.0, le=1.0, description="enb / N_strategies")
    n_strategies: int = Field(..., ge=1)
    n_observations: int = Field(..., ge=1)
    alpha: float = Field(0.975, gt=0.0, lt=1.0,
                         description="Cited: 19-portfolio-risk.md §4.1 Basel III FRTB")
    ts: datetime = Field(..., description="Audit timestamp (risk-rule-dsl.md §7)")
    cvar_levels: Optional[dict] = Field(
        default=None,
        description="Per-level CVaR dict from historical_cvar_levels(). Keys=label, values={alpha, cvar_pct}.",
    )


# ---------- pure numpy core ----------

def shrinkage_covariance(returns: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf shrinkage covariance.

    Args:
        returns: T×N array, rows=time, cols=assets, MUST be NaN-free.

    Returns:
        N×N symmetric PSD covariance matrix.

    Raises:
        ValueError: input not 2D or T < 2.
        RuntimeError: result not PSD (eig_min < -1e-10).

    Warnings:
        ShortSampleWarning if T < max(30, 2N).
    """
    if returns.ndim != 2:
        raise ValueError(f"returns must be 2D (T, N); got shape {returns.shape}")
    T, N = returns.shape
    if T < 2:
        raise ValueError(f"need T >= 2 observations; got T={T}")
    if T < max(30, 2 * N):
        warnings.warn(
            f"Short sample: T={T}, N={N}; LW estimate may be noisy (§2.2).",
            ShortSampleWarning, stacklevel=2,
        )
    lw = LedoitWolf().fit(returns)
    cov = np.asarray(lw.covariance_, dtype=float)
    cov = 0.5 * (cov + cov.T)
    eig_min = float(np.linalg.eigvalsh(cov).min())
    if eig_min < -1e-10:
        raise RuntimeError(f"LW covariance not PSD: eig_min={eig_min}")
    return cov


def historical_cvar(returns: np.ndarray, alpha: float = 0.975) -> float:
    """Left-tail CVaR. Returns POSITIVE loss fraction.

    Args:
        returns: 1D array of returns (positive = gain, negative = loss).
        alpha: confidence level, default 0.975 (Basel III FRTB §4.1).

    Returns:
        Mean of returns in worst (1-alpha) tail, negated so loss is positive.
        N=1 degenerate case returns |returns[0]| if negative else 0.0.
    """
    r = np.asarray(returns, dtype=float).ravel()
    if r.size == 0:
        raise ValueError("returns is empty")
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha must be in (0,1); got {alpha}")
    q = float(np.quantile(r, 1.0 - alpha))
    tail = r[r <= q]
    if tail.size == 0:
        return float(-q)
    return float(-tail.mean())


_DEFAULT_CVAR_LEVELS: list[tuple[float, str]] = [
    (0.95, "warn"),
    (0.975, "reduce"),
    (0.99, "halt"),
]


def historical_cvar_levels(
    returns: np.ndarray,
    levels: list[tuple[float, str]] = _DEFAULT_CVAR_LEVELS,
) -> dict[str, dict]:
    """Compute historical CVaR at multiple alpha levels.

    Patent-avoidance: plain empirical tail-mean, no proprietary decomposition.

    Args:
        returns: 1D array of returns (positive = gain, negative = loss).
        levels: list of (alpha, label) pairs in ascending alpha order.
                Default: [(0.95,"warn"),(0.975,"reduce"),(0.99,"halt")].

    Returns:
        dict keyed by label, each value {"alpha": float, "cvar_pct": float}.
    """
    result: dict[str, dict] = {}
    for alpha, label in levels:
        result[label] = {"alpha": float(alpha), "cvar_pct": historical_cvar(returns, alpha)}
    return result


def effective_number_of_bets(weights: np.ndarray, cov: np.ndarray) -> float:
    """Meucci ENB — PCA entropy form (19-portfolio-risk.md §3.1).

        p_k = λ_k · v_k² / Σ_j λ_j v_j²,   v = V^T w   (V: eigvecs of Σ)
        ENB = exp(-Σ_k p_k log p_k)

    Bounds: 1 (fully concentrated) ≤ ENB ≤ N (fully diversified).
    Returns NaN if total variance contribution is non-positive (degenerate Σ).
    """
    w = np.asarray(weights, dtype=float).ravel()
    c = np.asarray(cov, dtype=float)
    if w.size != c.shape[0] or c.shape[0] != c.shape[1]:
        raise ValueError(f"shape mismatch: w={w.shape}, cov={c.shape}")
    eigvals, eigvecs = np.linalg.eigh(c)
    v = eigvecs.T @ w
    contributions = np.clip(eigvals, 0.0, None) * (v ** 2)
    total = float(contributions.sum())
    if total <= 0.0:
        return math.nan
    p = contributions / total
    p = p[p > 1e-15]
    if p.size == 0:
        return math.nan
    entropy = float(-np.sum(p * np.log(p)))
    return float(math.exp(entropy))


def average_pairwise_correlation(cov: np.ndarray) -> float:
    """Average of upper-triangular correlation entries. Clamp [-1, 1]. N=1 → 0.0."""
    c = np.asarray(cov, dtype=float)
    if c.ndim != 2 or c.shape[0] != c.shape[1]:
        raise ValueError(f"cov must be square 2D; got {c.shape}")
    N = c.shape[0]
    if N < 2:
        return 0.0
    std = np.sqrt(np.diag(c))
    if np.any(std <= 0.0):
        return 0.0
    corr = c / np.outer(std, std)
    corr = np.clip(corr, -1.0, 1.0)
    iu = np.triu_indices(N, k=1)
    vals = corr[iu]
    return float(np.nanmean(vals))


# ---------- pandas wrapper ----------

def compute_portfolio_risk_from_df(
    df: "pd.DataFrame",
    weights: Optional[np.ndarray] = None,
    alpha: float = 0.975,
    ts: Optional[datetime] = None,
) -> PortfolioRiskReport:
    """Convenience: pd.DataFrame (T×N, rows=time, cols=strategy_id) → PortfolioRiskReport.

    - NaN rows dropped (row-wise any).
    - Default weights = equal weight 1/N.
    - T >= 2 required after dropna.
    """
    import pandas as pd
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"df must be pd.DataFrame; got {type(df).__name__}")
    clean = df.dropna(axis=0, how="any").to_numpy(dtype=float)
    if clean.ndim != 2:
        raise ValueError("df must be 2D after dropna")
    T, N = clean.shape
    if T < 2:
        raise ValueError(f"need T>=2 observations after dropna; got T={T}")

    cov = shrinkage_covariance(clean)

    if weights is None:
        w = np.full(N, 1.0 / N)
    else:
        w = np.asarray(weights, dtype=float).ravel()
        if w.size != N:
            raise ValueError(f"weights length {w.size} != N={N}")

    portfolio_returns = clean @ w
    var_pct = float(-np.quantile(portfolio_returns, 1.0 - alpha))
    var_pct = max(var_pct, 0.0)
    cvar_pct = historical_cvar(portfolio_returns, alpha)
    cvar_pct = max(cvar_pct, 0.0)

    enb_raw = effective_number_of_bets(w, cov)
    if math.isnan(enb_raw):
        enb_val = 0.0
        enb_ratio = 0.0
    else:
        enb_val = float(enb_raw)
        enb_ratio = min(enb_val / N, 1.0)

    corr_avg = average_pairwise_correlation(cov)

    return PortfolioRiskReport(
        cvar_pct=cvar_pct,
        var_pct=var_pct,
        corr_avg=corr_avg,
        enb=enb_val,
        enb_ratio=enb_ratio,
        n_strategies=N,
        n_observations=T,
        alpha=alpha,
        ts=ts or datetime.now(timezone.utc),
    )
