"""Strategy orchestrator stub — interface contract for issue #78.

Responsibilities (current scope — sync):
1. Collect per-strategy daily return series (one Series per strategy_id).
2. Stack them into a T×N DataFrame and feed to
   ``risk.compute_portfolio_risk_from_df`` to produce a PortfolioRiskReport.
3. Evaluate single-order intents by injecting the latest report into a
   Snapshot and calling ``risk.evaluate(policy, snap)``.

Out of scope (issue #78 will add):
- asyncio event loop / ``run_bar`` tick driver
- Signal → PositionSizer wiring (depends on #69, #76)
- OrderIntent emission batch + idempotency-key threading

Not callable from LLM tool surface (CLAUDE.md invariant #6).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from risk import (
    Policy,
    Snapshot,
    Order,
    Decision,
    evaluate,
    PortfolioRiskReport,
    compute_portfolio_risk_from_df,
)


class StrategyOrchestrator:
    """Synchronous stub. Aggregates strategy returns, gates orders via risk DSL.

    Typical usage::

        orch = StrategyOrchestrator(policy)
        orch.register_strategy_returns("momo_btc_v2", ret_series_a)
        orch.register_strategy_returns("meanrev_pairs", ret_series_b)
        orch.refresh_portfolio_risk(ts=now)
        decision = orch.evaluate_order(
            Order(symbol="BTCUSDT", side="buy", qty=0.01, price=50_000),
            equity_krw=100_000_000,
        )
    """

    def __init__(self, policy: Policy) -> None:
        self._policy = policy
        self._returns: dict[str, pd.Series] = {}
        self._report: Optional[PortfolioRiskReport] = None

    # ---- strategy-side API -------------------------------------------------

    def register_strategy_returns(self, strategy_id: str, series: pd.Series) -> None:
        """Register a daily return series for a strategy.

        Contract (enforced by downstream risk module):
        - ``series.index`` is datetime-like (daily frequency)
        - values are fractional returns (e.g. 0.01 = +1%)
        - NaN handling is delegated to the wrapper (dropna row-wise).
        """
        if not isinstance(series, pd.Series):
            raise TypeError(f"series must be pd.Series; got {type(series).__name__}")
        self._returns[strategy_id] = series.rename(strategy_id)

    def registered_strategies(self) -> list[str]:
        return list(self._returns.keys())

    # ---- risk-side API -----------------------------------------------------

    def refresh_portfolio_risk(
        self, ts: Optional[datetime] = None
    ) -> Optional[PortfolioRiskReport]:
        """Recompute the portfolio risk report from all registered returns.

        Returns None (and clears internal report) when N<2 strategies or
        the aligned return frame has fewer than 2 observations — the
        portfolio risk metrics are undefined in those regimes.
        """
        if len(self._returns) < 2:
            self._report = None
            return None

        df = pd.concat(
            list(self._returns.values()),
            axis=1,
            keys=list(self._returns.keys()),
        ).dropna(how="any")

        if len(df) < 2:
            self._report = None
            return None

        self._report = compute_portfolio_risk_from_df(df, ts=ts)
        return self._report

    @property
    def current_report(self) -> Optional[PortfolioRiskReport]:
        return self._report

    def evaluate_order(
        self,
        intent: Order,
        equity_krw: float,
        **snap_extras,
    ) -> Decision:
        """Evaluate an order intent against policy + latest portfolio risk.

        ``snap_extras`` is forwarded to ``Snapshot(**extras)`` so callers can
        populate optional fields (``day_orders``, ``sector_weights_pct``, etc.)
        without needing to know this stub's internals.
        """
        snap = Snapshot(
            intent=intent,
            equity_krw=equity_krw,
            portfolio_risk=self._report,
            **snap_extras,
        )
        return evaluate(self._policy, snap)
