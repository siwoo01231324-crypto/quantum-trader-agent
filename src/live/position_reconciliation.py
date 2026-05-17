"""Logical vs exchange position reconciliation (#238 Item 5 — safe core).

Binance USDⓈ-M Futures in one-way mode holds exactly **one net position per
symbol**. This system, however, tracks per-strategy *logical* positions
(`StrategyPositionStore`) and the dashboard rendered them as if each were a
real exchange position. In the incident, momo-btc-v2's ``-1`` BTC short
netted away the live-scanner ``+0.05`` "longs" — those longs never existed on
the exchange, yet were shown (and a live-scanner take-profit on one would
have *added* to the short, not closed a long).

This module does **not** re-architect attribution (that is a design-first
effort, intentionally out of scope here). It is the **reconciliation safety
net**: a pure, deterministic comparison of Σ(logical per symbol) against the
broker's actual net per symbol, so the divergence is *surfaced* instead of
silently misleading the operator.

``delta = logical_net - broker_net`` (how much the logical book thinks it
holds beyond what the exchange actually shows; negative = logical is shorter
than the exchange).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ReconcileMismatch:
    symbol: str
    logical_net: Decimal
    broker_net: Decimal
    delta: Decimal  # logical_net - broker_net


def sum_logical_by_symbol(
    logical: dict[str, dict[str, Decimal]],
) -> dict[str, Decimal]:
    """Collapse ``{strategy_id: {symbol: qty}}`` to ``{symbol: net_qty}``.

    This is the operation Binance one-way netting performs implicitly — many
    per-strategy logical legs become a single exchange position per symbol.
    """
    out: dict[str, Decimal] = {}
    for bucket in logical.values():
        for symbol, qty in bucket.items():
            out[symbol] = out.get(symbol, Decimal("0")) + qty
    return out


def reconcile_positions(
    logical: dict[str, dict[str, Decimal]],
    broker_net: dict[str, Decimal],
    *,
    tol: Decimal,
) -> list[ReconcileMismatch]:
    """Return per-symbol mismatches where ``|logical - broker| > tol``.

    Symbols absent on either side are treated as a flat ``0`` position, so a
    phantom logical holding or an unattributed exchange position is flagged.
    Deterministic ordering: sorted by symbol.
    """
    logical_net = sum_logical_by_symbol(logical)
    symbols = sorted(set(logical_net) | set(broker_net))
    mismatches: list[ReconcileMismatch] = []
    for symbol in symbols:
        lnet = logical_net.get(symbol, Decimal("0"))
        bnet = broker_net.get(symbol, Decimal("0"))
        delta = lnet - bnet
        if abs(delta) > tol:
            mismatches.append(
                ReconcileMismatch(
                    symbol=symbol,
                    logical_net=lnet,
                    broker_net=bnet,
                    delta=delta,
                )
            )
    return mismatches
