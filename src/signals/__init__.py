"""Stateless signal computation functions + factor registry."""
from . import registry  # must import first so decorators populate FACTOR_REGISTRY
from .registry import (
    DEFAULT_FACTOR_SET,
    FACTOR_REGISTRY,
    FactorSpec,
    compute,
    list_factors,
    register,
)
from .rsi import compute_rsi, detect_divergence
from .sma import compute_sma, compute_sma_cross
from .atr import compute_atr
from .macd import compute_macd
from .bollinger import compute_bollinger
from .realized_vol import compute_realized_vol

__all__ = [
    "DEFAULT_FACTOR_SET",
    "FACTOR_REGISTRY",
    "FactorSpec",
    "compute",
    "compute_atr",
    "compute_bollinger",
    "compute_macd",
    "compute_realized_vol",
    "compute_rsi",
    "compute_sma",
    "compute_sma_cross",
    "detect_divergence",
    "list_factors",
    "register",
    "registry",
]
