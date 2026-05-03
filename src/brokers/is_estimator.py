"""Implementation Shortfall pre-flight estimator + post-fill realized IS.

Formula (Perold 1988 simple parametric — patent #84-4 차용, BlackRock US12067619B1):
    IS_est_bps  = spread_bps/2 + market_impact_coeff * sqrt(qty/adv) * 10000
    realized_IS = (fill_price - arrival_price) / arrival_price * 10000  (BUY)
               = (arrival_price - fill_price) / arrival_price * 10000  (SELL)

Patent avoidance: only measurement/logging adopted (BlackRock (c)(d)).
Component (b) "execution style probability (Auto/RFQ/Voice)" NOT adopted.
IS formula differs from BlackRock approach — simple parametric, not ML-based.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from src.brokers.types import BrokerFill
from src.execution.base import Side


@dataclass(frozen=True)
class MarketSnapshot:
    """Minimal market state needed for IS pre-flight estimate."""
    symbol: str
    bid: Decimal
    ask: Decimal
    adv: float  # average daily volume (shares/units); 0 disables market impact term


def pre_flight_is_estimate(
    symbol: str,
    side: Side,
    qty: int | float,
    snap: MarketSnapshot,
    market_impact_coeff: float = 0.1,
) -> float:
    """Estimate Implementation Shortfall in basis points before order submission.

    Args:
        symbol: instrument identifier (unused in formula, retained for logging)
        side: BUY or SELL (IS estimate is symmetric — same cost either direction)
        qty: order quantity in shares/units
        snap: current bid/ask/adv snapshot
        market_impact_coeff: σ-coefficient for sqrt(qty/adv) market impact term

    Returns:
        Estimated IS in basis points (always >= 0)
    """
    mid = (snap.bid + snap.ask) / 2
    if mid == 0:
        return 0.0

    spread_bps = float((snap.ask - snap.bid) / mid * 10000)
    half_spread_bps = spread_bps / 2.0

    if snap.adv > 0:
        participation = qty / snap.adv
        market_impact_bps = market_impact_coeff * math.sqrt(participation) * 10000
    else:
        market_impact_bps = 0.0

    return half_spread_bps + market_impact_bps


def realized_is(
    fill: BrokerFill,
    arrival_price: Decimal,
    side: Side,
) -> float:
    """Compute realized Implementation Shortfall in basis points after fill.

    Positive IS means adverse execution (paid more for BUY / received less for SELL).
    Negative IS means price improvement.

    Args:
        fill: completed BrokerFill with actual execution price
        arrival_price: mid-price at order submission (decision price)
        side: BUY or SELL

    Returns:
        Realized IS in basis points
    """
    if arrival_price == 0:
        return 0.0

    if side == Side.BUY:
        return float((fill.price - arrival_price) / arrival_price * 10000)
    else:
        return float((arrival_price - fill.price) / arrival_price * 10000)
