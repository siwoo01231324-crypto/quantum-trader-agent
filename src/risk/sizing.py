"""Position sizing — Kelly, fractional Kelly, volatility targeting.

Pure mathematical functions. No I/O, no network, no LLM calls.

Inputs are deterministic numerics (p, b, mu, sigma, returns); outputs are
floats in [0, 1] representing the fraction of equity to allocate.

Final policy-level clamping (per_position.max_weight_pct, max_leverage, etc.)
is the responsibility of `risk.dsl.evaluate`, not this module.

References:
- docs/background/20-position-sizing.md
- docs/specs/position-sizing.md
- CLAUDE.md invariant #6 — LLM must not make risk/sizing decisions.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
import pandas as pd


def _clamp_unit(x: float) -> float:
    """Clamp to [0.0, 1.0]. NaN -> 0.0 (fail-closed)."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return 0.0
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)


def kelly_binary(p: float, b: float) -> float:
    """Kelly fraction for a binary outcome.

    f* = (b * p - (1 - p)) / b

    Args:
        p: win probability in [0, 1].
        b: payoff ratio (net odds received on a win), must be > 0.
            Example: b=1.0 means even-money (win $1 per $1 risked).

    Returns:
        Kelly fraction clamped to [0, 1]. Negative edge (p*b < 1-p) returns 0.

    Raises:
        ValueError: p not in [0, 1] or b <= 0.
    """
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"p must be in [0, 1], got {p}")
    if b <= 0.0:
        raise ValueError(f"b must be > 0, got {b}")
    f = (b * p - (1.0 - p)) / b
    return _clamp_unit(f)


def kelly_continuous(mu: float, sigma: float, rf: float = 0.0) -> float:
    """Kelly fraction for continuous (Gaussian) returns.

    f* = (mu - rf) / sigma^2

    Args:
        mu: expected period return.
        sigma: return standard deviation (same period as mu), must be >= 0.
        rf: risk-free rate for the same period. Default 0.

    Returns:
        Kelly fraction clamped to [0, 1]. sigma == 0 or edge <= 0 returns 0
        (fail-closed: without variance we cannot justify allocation; without
        positive edge there is no reason to bet).

    Raises:
        ValueError: sigma < 0.
    """
    if sigma < 0.0:
        raise ValueError(f"sigma must be >= 0, got {sigma}")
    if sigma == 0.0:
        return 0.0
    edge = mu - rf
    if edge <= 0.0:
        return 0.0
    f = edge / (sigma * sigma)
    return _clamp_unit(f)


def fractional_kelly(full_kelly: float, k: float = 0.5) -> float:
    """Apply a Kelly fraction multiplier.

    Half Kelly (k=0.5) keeps ~75% of Full Kelly's long-term growth with
    roughly half the variance (Thorp 1997). k=0.25 (Quarter Kelly) is
    another common choice when signal confidence is lower.

    Args:
        full_kelly: raw Kelly fraction (e.g., from kelly_binary / kelly_continuous).
        k: multiplier in (0, 1]. Default 0.5 (Half Kelly).

    Returns:
        k * full_kelly, clamped to [0, 1].

    Raises:
        ValueError: k not in (0, 1].
    """
    if not 0.0 < k <= 1.0:
        raise ValueError(f"k must be in (0, 1], got {k}")
    return _clamp_unit(k * full_kelly)


def vol_target(
    sigma_period: float,
    target_annual: float = 0.10,
    periods_per_year: int = 252,
) -> float:
    """Volatility-targeted weight.

    w = target_annual / (sigma_period * sqrt(periods_per_year))

    Scales the position so annualized portfolio volatility equals target_annual
    (under the single-asset assumption).

    Args:
        sigma_period: per-period return standard deviation (e.g., daily for KR
            equities, 15m for BTC 15m bars). Must be >= 0.
        target_annual: target annualized volatility. Default 0.10 (10%),
            the recommended value for KR mid/large cap per
            docs/background/20-position-sizing.md §3.3. For crypto, callers
            typically override to ~0.20.
        periods_per_year: annualization factor. 252 (equities), 365 (daily crypto),
            365*96=35040 (15m crypto bars), etc.

    Returns:
        Weight clamped to [0, 1]. sigma_period == 0 returns 1.0 (full
        allocation; the final per_position.max_weight_pct in the risk policy
        is what actually bounds exposure).

    Raises:
        ValueError: sigma_period < 0, target_annual <= 0, or periods_per_year <= 0.
    """
    if sigma_period < 0.0:
        raise ValueError(f"sigma_period must be >= 0, got {sigma_period}")
    if target_annual <= 0.0:
        raise ValueError(f"target_annual must be > 0, got {target_annual}")
    if periods_per_year <= 0:
        raise ValueError(f"periods_per_year must be > 0, got {periods_per_year}")
    if sigma_period == 0.0:
        return 1.0
    sigma_annual = sigma_period * math.sqrt(periods_per_year)
    w = target_annual / sigma_annual
    return _clamp_unit(w)


def ewma_sigma(
    returns: Sequence[float] | pd.Series | np.ndarray,
    lam: float = 0.94,
) -> float:
    """RiskMetrics EWMA standard deviation of period returns.

    Recursively: var_t = lam * var_{t-1} + (1 - lam) * r_t^2
    (assumes zero mean, the RiskMetrics convention.)

    Args:
        returns: sequence of period returns (already differenced). NaN values
            are dropped.
        lam: decay factor in (0, 1). Default 0.94 is the RiskMetrics 1996
            standard for daily data.

    Returns:
        sqrt(var_T), the EWMA standard deviation at the last sample. Returns
        0.0 if fewer than 2 valid samples.

    Raises:
        ValueError: lam not in (0, 1).
    """
    if not 0.0 < lam < 1.0:
        raise ValueError(f"lam must be in (0, 1), got {lam}")

    arr = np.asarray(returns, dtype=float).ravel()
    arr = arr[~np.isnan(arr)]
    if arr.size < 2:
        return 0.0

    var = 0.0
    for r in arr:
        var = lam * var + (1.0 - lam) * float(r) * float(r)
    return math.sqrt(var)
