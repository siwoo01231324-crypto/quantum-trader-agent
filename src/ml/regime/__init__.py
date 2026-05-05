"""Regime detection module — HMM and threshold-based market regime classification.

Hamilton (1989) 2-state HMM for volatility regime detection,
plus simple threshold-based classifiers as baselines.

HMM exports are lazy-loaded so that environments without hmmlearn
(e.g. Python 3.14 prebuilt wheels not yet available) can still use the
threshold-only paths. Importing GaussianHMMRegime / RegimeResult will
trigger the hmmlearn import on demand.
"""
from __future__ import annotations

from src.ml.regime.threshold import ThresholdRegime

__all__ = [
    "GaussianHMMRegime",
    "RegimeResult",
    "ThresholdRegime",
]


def __getattr__(name: str):
    if name in ("GaussianHMMRegime", "RegimeResult"):
        from src.ml.regime.hmm import GaussianHMMRegime, RegimeResult
        globals()["GaussianHMMRegime"] = GaussianHMMRegime
        globals()["RegimeResult"] = RegimeResult
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
