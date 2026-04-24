"""Risk rule DSL + position sizing + portfolio-level metrics.

- dsl:       pydantic models, YAML loader, single-order evaluator (#24, #70)
- sizing:    Kelly / Fractional Kelly / Vol Targeting / EWMA σ pure functions (#69)
- portfolio: LW shrinkage Σ / Historical CVaR / Meucci ENB / avg ρ (#70)
"""
from .dsl import (
    Policy,
    PerTrade,
    PerDay,
    PerPortfolio,
    PerPortfolioRisk,
    PerPosition,
    SectorLimit,
    Drawdown,
    Snapshot,
    Order,
    Decision,
    Action,
    load_policy,
    evaluate,
)
from .sizing import (
    kelly_binary,
    kelly_continuous,
    fractional_kelly,
    vol_target,
    ewma_sigma,
    user_risk_vol_target,
    consensus_kelly,
)
from .portfolio import (
    PortfolioRiskReport,
    ShortSampleWarning,
    shrinkage_covariance,
    historical_cvar,
    effective_number_of_bets,
    average_pairwise_correlation,
    compute_portfolio_risk_from_df,
)

__all__ = [
    # DSL
    "Policy", "PerTrade", "PerDay", "PerPortfolio", "PerPortfolioRisk",
    "PerPosition", "SectorLimit", "Drawdown", "Snapshot", "Order", "Decision",
    "Action", "load_policy", "evaluate",
    # Sizing (#69, #87)
    "kelly_binary", "kelly_continuous", "fractional_kelly",
    "vol_target", "ewma_sigma", "user_risk_vol_target", "consensus_kelly",
    # Portfolio (#70)
    "PortfolioRiskReport", "ShortSampleWarning",
    "shrinkage_covariance", "historical_cvar",
    "effective_number_of_bets", "average_pairwise_correlation",
    "compute_portfolio_risk_from_df",
]
