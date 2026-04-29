"""ML evaluation metrics for MetaLabeler cross-validation.

Provides scoring functions that complement src/backtest/metrics.py (equity-curve
metrics) without collision — functions here operate on returns arrays or
classifier outputs, not equity curves.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm


# ---------------------------------------------------------------------------
# Sharpe utilities
# ---------------------------------------------------------------------------

def annualized_sharpe(returns: pd.Series, periods_per_year: int) -> float:
    """Annualized Sharpe ratio from a returns series.

    Parameters
    ----------
    returns:
        Per-period returns (e.g. bar-level or daily).
    periods_per_year:
        Number of periods in one year (e.g. 252 daily, 6552 hourly KRX,
        35040 5-min Binance).

    Returns
    -------
    float
        0.0 when std == 0 or the series is empty.
    """
    if len(returns) == 0:
        return 0.0
    std = float(returns.std(ddof=1))
    if std == 0.0:
        return 0.0
    return float(returns.mean()) / std * np.sqrt(periods_per_year)


# ---------------------------------------------------------------------------
# Deflated Sharpe Ratio  (Bailey & López de Prado 2014)
# ---------------------------------------------------------------------------

def deflated_sharpe_ratio(
    observed_sr: float,
    sr_estimates: list[float],
    n_trials: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Probability that the observed Sharpe Ratio is a false discovery.

    Implements the DSR formula from Bailey & López de Prado (2014):
    "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest
    Overfitting and Non-Normality."

    Parameters
    ----------
    observed_sr:
        The Sharpe ratio being tested.
    sr_estimates:
        Pool of SR estimates used to calibrate the null distribution.
        Must be non-empty.
    n_trials:
        Number of strategy trials (backtest configurations) evaluated.
        Use 1 to skip deflation (DSR ≈ raw Sharpe significance test).
    skew:
        Skewness of the returns distribution (0 → Gaussian).
    kurtosis:
        Excess kurtosis of the returns distribution (3 → Gaussian).

    Returns
    -------
    float
        DSR value in [0, 1] — probability that the SR is genuine.

    Notes
    -----
    For n_trials == 1 the expected maximum SR_null collapses to ~0, so DSR
    approaches the significance of the raw Sharpe (essentially 1.0 for
    typical values).  Tests should verify DSR(n_trials=1) >= DSR(n_trials>1).
    """
    if not sr_estimates:
        raise ValueError("sr_estimates must be non-empty")

    T = len(sr_estimates)

    # Variance of SR estimator (moment correction from López de Prado)
    # Var(SR) ≈ (1 - skew·SR + (kurt-1)/4 · SR²) / (T-1)
    # We use observed_sr for the SR in the variance term.
    T_eff = max(T - 1, 1)
    var_sr = (1.0 - skew * observed_sr + (kurtosis - 1.0) / 4.0 * observed_sr ** 2) / T_eff

    # Expected maximum SR under the null (selection bias correction)
    if n_trials <= 1:
        # No selection bias — null mean is 0
        e_max = 0.0
    else:
        # López de Prado approximation of E[max(SR_null)] for n_trials iid draws
        # γ ≈ 0.5772 (Euler-Mascheroni constant)
        gamma = 0.5772156649
        e_max = (
            (1.0 - gamma) * norm.ppf(1.0 - 1.0 / n_trials)
            + gamma * norm.ppf(1.0 - 1.0 / (n_trials * np.e))
        )

    # DSR = Φ((SR_observed - E_max) / sqrt(Var_SR))
    if var_sr <= 0.0:
        # Degenerate case — SR estimate is deterministic
        return 1.0 if observed_sr > e_max else 0.0

    z = (observed_sr - e_max) / np.sqrt(var_sr)
    return float(norm.cdf(z))


# ---------------------------------------------------------------------------
# Drawdown
# ---------------------------------------------------------------------------

def max_drawdown(equity: pd.Series) -> float:
    """Maximum drawdown from peak-to-trough as a positive fraction.

    Returns 0.0 for empty series or flat equity.
    """
    if len(equity) == 0:
        return 0.0
    peak = equity.cummax()
    # Avoid division by zero for zero-valued equity
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(peak != 0, (peak - equity) / peak, 0.0)
    return float(np.max(dd))


# ---------------------------------------------------------------------------
# PR-AUC wrapper
# ---------------------------------------------------------------------------

def pr_auc_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    """Area under the Precision-Recall curve.

    Thin wrapper around sklearn.metrics.average_precision_score imported
    lazily to avoid hard dependency at module load time.

    Parameters
    ----------
    y_true:
        Binary ground-truth labels {0, 1}.
    y_prob:
        Predicted probabilities for the positive class.

    Returns
    -------
    float
        PR-AUC in [0, 1].
    """
    from sklearn.metrics import average_precision_score  # noqa: PLC0415
    return float(average_precision_score(y_true, y_prob))


# ---------------------------------------------------------------------------
# Sharpe improvement ratio
# ---------------------------------------------------------------------------

def sharpe_improvement_ratio(sr_on: float, sr_off: float) -> float:
    """Ratio of meta-labeler ON Sharpe to OFF Sharpe.

    Returns 0.0 when sr_off == 0 to avoid division by zero.
    A value > 1.0 indicates the meta-labeler improves risk-adjusted returns.
    """
    if sr_off == 0.0:
        return 0.0
    return sr_on / sr_off
