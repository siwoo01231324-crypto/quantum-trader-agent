"""Threshold-based regime classification — simple rule-based baseline.

No statistical model: uses rolling return and funding rate thresholds
to classify market regime. Serves as R4 baseline in the variant matrix.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ThresholdResult:
    """Container for threshold regime output."""

    states: np.ndarray
    labels: dict[int, str]


class ThresholdRegime:
    """Rule-based regime classifier using return and funding thresholds.

    Parameters
    ----------
    return_lookback : Rolling window for return calculation (bars).
    return_threshold : If rolling return > threshold, classify as 'bullish'.
    funding_threshold : If funding rate < threshold, classify as 'funding_negative'.
    """

    def __init__(
        self,
        return_lookback: int = 180,
        return_threshold: float = 0.0,
        funding_threshold: float = 0.0,
    ) -> None:
        self.return_lookback = return_lookback
        self.return_threshold = return_threshold
        self.funding_threshold = funding_threshold

    def classify(
        self,
        close: pd.Series,
        funding_rate: pd.Series | None = None,
    ) -> ThresholdResult:
        """Classify each bar into a regime.

        States:
            0 = bullish (rolling return > threshold) -> favor trend-following (S2c)
            1 = funding_negative (funding < threshold) -> favor carry (S4)
            2 = neutral (neither condition met)

        When funding_rate is None, only return-based classification is used,
        and state 1 is never assigned.
        """
        rolling_ret = close.pct_change(self.return_lookback).shift(1)

        states = np.full(len(close), 2, dtype=int)

        bullish_mask = (rolling_ret > self.return_threshold).fillna(False).to_numpy()
        states[bullish_mask] = 0

        if funding_rate is not None:
            funding_shifted = funding_rate.shift(1)
            funding_neg = (funding_shifted < self.funding_threshold).fillna(False).to_numpy()
            non_bullish = ~bullish_mask
            states[non_bullish & funding_neg] = 1

        labels = {0: "bullish", 1: "funding_negative", 2: "neutral"}
        return ThresholdResult(states=states, labels=labels)
