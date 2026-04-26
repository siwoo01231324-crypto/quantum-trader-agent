"""KIS fills + balance → strategy daily returns → register_strategy_returns.

KIS API returns KRW directly. This module computes per-strategy daily return
series from fill records and balance snapshots, then registers them with the
AsyncStrategyOrchestrator so portfolio CVaR/ENB/correlation checks are not silenced.

CLAUDE.md invariant: register_strategy_returns MUST be called for every active
strategy; omission causes portfolio risk to silently skip that strategy.

Architect note #4 — call-site policy (chosen: shutdown hook in scripts/live_run.py):
  The caller (live_run.py) invokes export_to_orchestrator() once per trading day
  at daemon shutdown or on a daily cron. This module exposes callable functions
  only; it does not schedule itself.

Primary source of KRW values: KIS fills + balance polling (KIS returns KRW natively).
fx_rate (USD/KRW) is imported for reference only; if it returns None (>24h stale)
daily returns are still computed from raw KRW equity — KRW metrics are not suppressed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from src.observability.fx_rate import get_usd_krw

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KisFillRecord:
    """Single KIS WS fill event used for PnL attribution."""

    broker_order_id: str
    fill_price: Decimal
    fill_qty: Decimal
    side: str          # "BUY" | "SELL"
    ts: datetime       # UTC fill timestamp
    strategy_id: str


def compute_daily_returns(
    fills: list[KisFillRecord],
    balance_history: list[tuple[date, Decimal]],
    strategy_id: str,
) -> pd.Series:
    """Compute per-strategy daily return series from KIS equity snapshots.

    Args:
        fills: List of KIS fill records for the strategy (used for future attribution;
               balance_history is the primary source of equity).
        balance_history: Ordered list of (date, equity_krw) snapshots.
        strategy_id: Strategy identifier (used as series name).

    Returns:
        pd.Series with DatetimeIndex (UTC date) and float daily returns.
        Returns empty Series (dtype=float) when balance_history has < 2 rows.

    Note:
        KIS returns KRW natively. fx_rate is fetched for reference logging only;
        if None (stale >24h), returns are still computed in KRW.
    """
    fx = get_usd_krw()
    if fx is None:
        logger.warning(
            "strategy_returns_export: fx_rate unavailable (>24h stale); "
            "computing daily returns in raw KRW for strategy=%s",
            strategy_id,
        )

    if len(balance_history) < 2:
        logger.info(
            "strategy_returns_export: insufficient balance history (%d rows) for strategy=%s; "
            "returning empty series — register_strategy_returns will still be called",
            len(balance_history),
            strategy_id,
        )
        return pd.Series(dtype=float, name=strategy_id)

    dates = [pd.Timestamp(d, tz="UTC") for d, _ in balance_history]
    equities = [float(eq) for _, eq in balance_history]

    equity_series = pd.Series(equities, index=dates, dtype=float)
    daily_returns = equity_series.pct_change().dropna()
    daily_returns.name = strategy_id

    return daily_returns


def export_to_orchestrator(
    orchestrator: Any,
    strategy_id: str,
    series: pd.Series,
) -> None:
    """Call orchestrator.register_strategy_returns(strategy_id, series).

    Always called — even with an empty series — to prevent portfolio CVaR/ENB
    from silently skipping this strategy (CLAUDE.md "register_strategy_returns 필수").

    Call-site policy (Architect note #4):
        Invoked by scripts/live_run.py at daemon shutdown (once per trading day)
        or via daily cron. This function does not schedule itself.
    """
    orchestrator.register_strategy_returns(strategy_id, series)
    logger.info(
        "strategy_returns_export: registered %d daily returns for strategy=%s",
        len(series),
        strategy_id,
    )
