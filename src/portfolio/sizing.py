"""Portfolio-level position sizing.

Bridges Signal metadata (expected_return / win_probability) to Kelly fractions.
Kelly math lives in src/risk/sizing.py — this module routes Signal fields to it.

NOT the same as src/risk/sizing.py (Kelly math primitives).
"""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_DOWN
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from risk.sizing import kelly_binary, kelly_continuous, ewma_sigma

if TYPE_CHECKING:
    from backtest.protocol import Signal

logger = logging.getLogger(__name__)

# #238 — Binance USDⓈ-M Futures MIN_NOTIONAL. The exchange rejects any order
# whose qty*price is below ~5 USDT (the documented majority value across USDT
# perps; exotic pairs can be higher but never lower). We deliberately do NOT
# poll exchangeInfo here — this runs in the per-tick hot path, and a
# guaranteed-rejected order is the very class of bug #238 fixed. A
# conservative static floor drops such orders before they ever reach the
# broker. Per-symbol refinement (exchangeInfo) is a separate concern handled
# by src.brokers.binance.symbol_filters.SymbolFilters at the adapter layer.
BINANCE_MIN_NOTIONAL_USDT = Decimal("5")


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


def size_to_qty(
    fraction: float,
    *,
    equity: float,
    price: float,
    symbol: str,
) -> float | None:
    """Convert a resolved size *fraction* into a real coin/share quantity.

    ``resolve_size`` yields a fraction of available equity (or a Signal.size
    passthrough). The orchestrator previously used that fraction DIRECTLY as
    the coin quantity — so ``size=0.05`` ordered 0.05 coins (not 5% of equity)
    and momo ``sizing_mode:full`` (``size=1.0``) ordered 1.0 BTC literally
    (~$80k). That was the deeper cause of the -2019 Margin-insufficient flood.

    Conversion::

        qty_coins = (fraction * available_equity) / price

    then exchange filters are applied:

    - **LOT_SIZE step** — ROUND_DOWN to the symbol step via
      ``src.live.conversion.get_step_size`` (KRX 6-digit → 1, Binance USDT
      pair → 0.001 default). Step rounding is also re-applied as a final
      guard inside ``intent_to_order_request``; this is the primary cut.
    - **MIN_NOTIONAL** — for Binance USDT pairs, if ``qty*price`` is below the
      conservative ``BINANCE_MIN_NOTIONAL_USDT`` floor the order is *dropped*
      (``None``) rather than emitted as a guaranteed-rejected order (the very
      class of bug #238 fixed). KRX has no notional floor (share-lot only).
    - **zero-qty / unsupported / bad inputs** — dropped (``None``).

    Args:
        fraction: size fraction in [0, 1] (defensively clamped to ≤ 1.0).
        equity: venue-correct available equity (KRW for KRX, USDT for Binance).
        price: current price in the same currency as ``equity``.
        symbol: trading symbol (drives step + min-notional venue rules).

    Returns:
        The exchange-filtered coin/share quantity as a Python ``float``, or
        ``None`` when the order must be dropped (caller emits no OrderIntent).
    """
    if not (price > 0.0) or not (equity > 0.0):
        logger.info(
            "portfolio.sizing.drop reason=non_positive_input symbol=%s "
            "equity=%s price=%s",
            symbol, equity, price,
        )
        return None

    # Defensive clamp — a fraction > 1 must never over-allocate equity.
    frac = min(max(float(fraction), 0.0), 1.0)
    if frac <= 0.0:
        logger.info(
            "portfolio.sizing.drop reason=zero_fraction symbol=%s", symbol,
        )
        return None

    # Lazy import — src.live.conversion imports src.portfolio.order_intent, so
    # a module-level import here forms a portfolio↔live cycle. This mirrors the
    # project's established lazy-import pattern (account_info.py / loop.py).
    from src.live.conversion import get_step_size  # noqa: PLC0415

    step = get_step_size(symbol)
    if step is None:
        logger.info(
            "portfolio.sizing.drop reason=unsupported_symbol symbol=%s", symbol,
        )
        return None

    notional = Decimal(str(frac)) * Decimal(str(equity))
    raw_qty = notional / Decimal(str(price))
    qty = raw_qty.quantize(step, rounding=ROUND_DOWN)

    if qty <= 0:
        logger.info(
            "portfolio.sizing.drop reason=qty_rounds_to_zero symbol=%s "
            "raw_qty=%s step=%s",
            symbol, raw_qty, step,
        )
        return None

    # MIN_NOTIONAL — Binance USDT pairs only (KRX is share-lot, no floor).
    if symbol.endswith("USDT") and len(symbol) > len("USDT"):
        filled_notional = qty * Decimal(str(price))
        if filled_notional < BINANCE_MIN_NOTIONAL_USDT:
            logger.info(
                "portfolio.sizing.drop reason=below_min_notional symbol=%s "
                "notional=%s min=%s",
                symbol, filled_notional, BINANCE_MIN_NOTIONAL_USDT,
            )
            return None

    return float(qty)
