"""Probabilistic Sharpe Ratio (PSR) and Deflated Sharpe Ratio (DSR).

Bailey, D.H. & López de Prado, M. (2014). The Deflated Sharpe Ratio.
Journal of Portfolio Management, 40(5), 94-107.

The PSR estimates the probability that an observed Sharpe ratio is
statistically greater than a given benchmark, correcting for skewness
and excess kurtosis of the return distribution.

The DSR additionally corrects for selection bias from N parallel
backtest trials. Project gate: DSR >= 0.95.
"""
from __future__ import annotations

import math
import warnings

import numpy as np
from scipy.stats import norm

EULER_MASCHERONI = 0.577_215_664_901_532_9


def probabilistic_sharpe_ratio(
    observed_sr: float,
    sr_benchmark: float,
    n_obs: int,
    skew: float,
    kurtosis_excess: float,
) -> float:
    """Compute the Probabilistic Sharpe Ratio (PSR).

    Bailey & López de Prado (2014) Eq.4.

    Parameters
    ----------
    observed_sr:
        Annualised Sharpe ratio of the candidate strategy.
    sr_benchmark:
        Sharpe ratio benchmark to test against (often 0).
    n_obs:
        Number of return observations the SR was estimated from.
    skew:
        Sample skewness of returns.
    kurtosis_excess:
        Sample excess kurtosis of returns (i.e. kurtosis - 3).

    Returns
    -------
    float
        Probability in [0, 1] that the observed SR truly exceeds the
        benchmark. PSR >= 0.95 indicates statistical significance.

    Raises
    ------
    ValueError
        If ``n_obs`` < 2 (cannot compute SR standard error).
    """
    if n_obs < 2:
        raise ValueError(f"n_obs must be >= 2, got {n_obs}")
    if n_obs < 30:
        warnings.warn(
            f"PSR with n_obs={n_obs} (< 30) may be unstable",
            stacklevel=2,
        )
    if kurtosis_excess < -2:
        warnings.warn(
            f"kurtosis_excess={kurtosis_excess} below theoretical -2",
            stacklevel=2,
        )

    # Standard error of the Sharpe ratio under non-normality
    # (Mertens 2002, also AFML §14.2)
    var_sr = (
        1.0
        - skew * observed_sr
        + (kurtosis_excess / 4.0) * observed_sr ** 2
    ) / (n_obs - 1)

    if var_sr <= 0:
        # Degenerate case (unlikely with realistic inputs); fall back to 0.5
        return 0.5

    se_sr = math.sqrt(var_sr)
    z = (observed_sr - sr_benchmark) / se_sr
    return float(norm.cdf(z))


def deflated_sharpe_ratio(
    observed_sr: float,
    sr_estimates: "np.ndarray | list[float]",
    n_obs: int,
    skew: float,
    kurtosis_excess: float,
    n_trials: int | None = None,
) -> float:
    """Compute the Deflated Sharpe Ratio (DSR).

    Bailey & López de Prado (2014) Theorem 2.

    Parameters
    ----------
    observed_sr:
        Annualised SR of the best-performing trial.
    sr_estimates:
        Array of SRs from all N parallel trials. Used to estimate
        ``Var(SR)`` under the null.
    n_obs:
        Number of return observations per trial.
    skew:
        Sample skewness of the best trial's returns.
    kurtosis_excess:
        Sample excess kurtosis of the best trial's returns.
    n_trials:
        Override for N (defaults to ``len(sr_estimates)``). Useful when
        some trials are recorded as DATA_UNAVAILABLE and excluded.

    Returns
    -------
    float
        DSR in [0, 1]. DSR >= 0.95 means the observed SR survives
        multi-testing correction at the 5% level.
    """
    sr_arr = np.asarray(sr_estimates, dtype=float)
    if sr_arr.size == 0:
        raise ValueError("sr_estimates must be non-empty")

    n = int(n_trials) if n_trials is not None else int(sr_arr.size)
    if n < 1:
        raise ValueError(f"n_trials must be >= 1, got {n}")

    if n == 1:
        # Single trial: DSR collapses to PSR with benchmark 0
        return probabilistic_sharpe_ratio(
            observed_sr, 0.0, n_obs, skew, kurtosis_excess
        )

    # Variance of SRs under the null
    var_sr = float(np.var(sr_arr, ddof=1)) if sr_arr.size > 1 else 0.0
    if var_sr <= 0:
        # All trials produced identical SR. SR0 collapses to 0; PSR
        # with benchmark 0 is the natural fallback.
        return probabilistic_sharpe_ratio(
            observed_sr, 0.0, n_obs, skew, kurtosis_excess
        )

    sqrt_var = math.sqrt(var_sr)

    # Expected maximum SR under the null (Bailey & López de Prado Eq.5)
    z1 = norm.ppf(1.0 - 1.0 / n)
    z2 = norm.ppf(1.0 - 1.0 / (n * math.e))
    sr0 = sqrt_var * (
        (1.0 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2
    )

    return probabilistic_sharpe_ratio(
        observed_sr, sr0, n_obs, skew, kurtosis_excess
    )
