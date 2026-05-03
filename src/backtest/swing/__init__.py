"""Swing strategy candidates (S1-S5 + iter 3 variants) for issue #99.

Each strategy is a pure function producing a position signal series.
Stateful position tracking is handled by the bench script.
"""
from __future__ import annotations

from src.backtest.swing.atr import wilder_atr
from src.backtest.swing.strategies import (
    s1_tsmom,
    s2_donchian,
    s2_donchian_atr_stop,
    s2_donchian_hard_rr,
    s2_donchian_voltarget,
    s2c_x_s4_composite,
    s3_ema_pullback,
    s4_funding_both,
    s4_funding_carry,
    s5_pairs,
)

__all__ = [
    "wilder_atr",
    "s1_tsmom",
    "s2_donchian",
    "s2_donchian_atr_stop",
    "s2_donchian_hard_rr",
    "s2_donchian_voltarget",
    "s2c_x_s4_composite",
    "s3_ema_pullback",
    "s4_funding_both",
    "s4_funding_carry",
    "s5_pairs",
]
