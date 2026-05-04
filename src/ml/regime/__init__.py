"""Regime detection module — HMM and threshold-based market regime classification.

Hamilton (1989) 2-state HMM for volatility regime detection,
plus simple threshold-based classifiers as baselines.
"""
from __future__ import annotations

from src.ml.regime.hmm import GaussianHMMRegime, RegimeResult
from src.ml.regime.threshold import ThresholdRegime

__all__ = [
    "GaussianHMMRegime",
    "RegimeResult",
    "ThresholdRegime",
]
