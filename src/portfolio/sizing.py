"""Portfolio-level position sizing.

Bridges Signal metadata (expected_return / win_probability) to Kelly fractions.
Kelly math lives in src/risk/sizing.py — this module routes Signal fields to it.

NOT the same as src/risk/sizing.py (Kelly math primitives).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from risk.sizing import kelly_binary, kelly_continuous, ewma_sigma

if TYPE_CHECKING:
    from backtest.protocol import Signal


def resolve_size(signal: Signal, recent_returns: pd.Series | None) -> float:
    """Resolve position size from Signal metadata with Signal-wins precedence.

    Precedence:
    1. signal.expected_return is not None → kelly_continuous(mu=expected_return, sigma)
       Note: expected_return=0.0 is treated as 0.0 (explicit zero), not fallback.
    2. signal.win_probability is not None → kelly_binary(p=win_probability, b=1.0)
    3. Otherwise → return signal.size unchanged (backward-compatible path).

    Args:
        signal: Signal dataclass with optional expected_return / win_probability.
        recent_returns: recent period returns used to estimate sigma via EWMA.
            Pass None or empty Series when no history is available (sigma → 0.0).

    Returns:
        Position size fraction in [0, 1].
    """
    has_expected_return = signal.expected_return is not None
    has_win_prob = signal.win_probability is not None

    if not has_expected_return and not has_win_prob:
        return signal.size

    if recent_returns is not None and len(recent_returns) > 0:
        arr = recent_returns.dropna().values
    else:
        arr = np.array([])
    sigma = ewma_sigma(arr) if len(arr) >= 2 else 0.0

    if has_expected_return:
        mu = float(signal.expected_return)
        return kelly_continuous(mu=mu, sigma=sigma)

    p = float(signal.win_probability)
    return kelly_binary(p=p, b=1.0)
