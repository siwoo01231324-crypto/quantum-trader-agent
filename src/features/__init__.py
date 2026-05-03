"""Iranyi VWMA strategy feature modules (issue #99).

Deterministic, lookahead-free feature engineering for the 8-variant
factorial experiment. All features consume OHLCV (or order-book) input
and return ``pd.Series`` / ``pd.DataFrame``.

Re-uses ``src.signals.lookahead_guard.assert_no_lookahead`` for
causality verification — does NOT duplicate the guard.
"""
from __future__ import annotations

from src.features.vwma import vwma, vwma_cross
from src.features.ma_projection import (
    ema_curvature,
    ema_projection,
    ema_slope,
)
from src.features.multi_tf import multi_tf_alignment
from src.features.time_of_day import time_gate
from src.features.cross_sectional_rs import (
    compute_ubai,
    relative_strength,
    rs_quartile,
)
from src.features.poc import point_of_control
from src.features.orderbook_flow import (
    aggregate_orderbook_features,
    microprice_mid_gap,
    order_book_imbalance,
    order_flow_imbalance,
)

__all__ = [
    # vwma
    "vwma",
    "vwma_cross",
    # ma_projection
    "ema_slope",
    "ema_curvature",
    "ema_projection",
    # multi_tf
    "multi_tf_alignment",
    # time_of_day
    "time_gate",
    # cross_sectional_rs
    "relative_strength",
    "rs_quartile",
    "compute_ubai",
    # poc
    "point_of_control",
    # orderbook_flow
    "order_book_imbalance",
    "order_flow_imbalance",
    "microprice_mid_gap",
    "aggregate_orderbook_features",
]
