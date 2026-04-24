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

import logging
import math
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

from risk import (
    Policy,
    Snapshot,
    Order,
    Decision,
    evaluate,
    PortfolioRiskReport,
    compute_portfolio_risk_from_df,
)

logger = logging.getLogger(__name__)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


class _SyncStrategyOrchestrator:
    """Synchronous stub. Aggregates strategy returns, gates orders via risk DSL.

    Private — consumed by AsyncStrategyOrchestrator via composition (D1).
    Direct instantiation is discouraged; use AsyncStrategyOrchestrator instead.

    Typical usage::

        orch = _SyncStrategyOrchestrator(policy)
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

    def strategy_reliability_score(self, strategy_id: str) -> float:
        """Compute a composite reliability score in [0, 1] for a registered strategy.

        Patent reference: KR101139626B1 (우리투자증권, active). differs:
        multiplicative gate (not KR additive convex). Hard-zero at DD>=20% is
        discontinuous indicator not recoverable by log-additive decomposition
        without floor-truncation.

        Formula:
            reliability = convex_base * drawdown_gate

            convex_base = 0.4*h(T) + 0.4*Phi(t_IR) + 0.2*(1 - CVaR_breach_rate)
                h(T) = min(T/252, 1) * (1 if T >= 126 else 0.5)
                t_IR = mean(r) / std(r) * sqrt(T);  T < 20 -> return 0.0 (NaN guard)
                Phi = scipy.stats.norm.cdf
                CVaR_breach_rate = rolling 21-day 5% CVaR breach ratio

            drawdown_gate = clip01(1 - max_dd_pct / 0.20)

        Returns 0.0 if strategy_id is not registered or T < 20.
        """
        if strategy_id not in self._returns:
            return 0.0

        r = self._returns[strategy_id].dropna()
        T = len(r)

        if T < 20:
            return 0.0

        # h(T): history weight — penalise short track records
        h = min(T / 252.0, 1.0) * (1.0 if T >= 126 else 0.5)

        # t_IR: information-ratio t-statistic
        mu = float(r.mean())
        sigma = float(r.std(ddof=1))
        if sigma == 0.0:
            t_IR = 0.0
        else:
            t_IR = mu / sigma * math.sqrt(T)
        phi_t_IR = float(stats.norm.cdf(t_IR))

        # CVaR breach rate: rolling 21-day 5% CVaR, fraction of days that breach it
        window = 21
        if T >= window + 1:
            rolling_cvar = (
                r.rolling(window)
                .quantile(0.05)
            )
            # breach = day's return is worse than its rolling CVaR threshold
            breach = (r < rolling_cvar).astype(float)
            cvar_breach_rate = float(breach.iloc[window:].mean())
        else:
            cvar_breach_rate = 0.0

        convex_base = 0.4 * h + 0.4 * phi_t_IR + 0.2 * (1.0 - cvar_breach_rate)

        # Max drawdown gate: hard-zero at DD >= 20%
        cum = (1.0 + r).cumprod()
        rolling_max = cum.cummax()
        drawdowns = (cum - rolling_max) / rolling_max
        max_dd_pct = float(abs(drawdowns.min()))
        drawdown_gate = _clamp01(1.0 - max_dd_pct / 0.20)

        score = _clamp01(convex_base * drawdown_gate)
        logger.info(
            "portfolio.reliability strategy_id=%s score=%.3f T=%d",
            strategy_id,
            score,
            T,
        )
        return score

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


# Backward-compat alias — #70 tests import StrategyOrchestrator directly.
StrategyOrchestrator = _SyncStrategyOrchestrator
