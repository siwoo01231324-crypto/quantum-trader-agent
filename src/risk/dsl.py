"""Risk rule DSL — pydantic schema, YAML loader, evaluation function.

Evaluation returns the first-violation Decision. Full block list and precedence:
see docs/specs/risk-rule-dsl.md §2.2.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt

from .portfolio import PortfolioRiskReport


class Action(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    REDUCE = "reduce"
    HALT = "halt"
    FLATTEN = "flatten"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PerTrade(_Strict):
    max_notional_krw: Optional[PositiveFloat] = None
    max_qty: Optional[PositiveInt] = None
    allowed_sides: Optional[list[str]] = None  # ['buy','sell']


class PerDay(_Strict):
    max_orders: Optional[PositiveInt] = None
    max_loss_krw: Optional[PositiveFloat] = None
    max_turnover_krw: Optional[PositiveFloat] = None


class PerPortfolio(_Strict):
    max_gross_exposure_krw: Optional[PositiveFloat] = None
    max_net_exposure_krw: Optional[PositiveFloat] = None
    max_leverage: Optional[PositiveFloat] = None


class PerPortfolioRisk(_Strict):
    """Portfolio-level risk thresholds evaluated against Snapshot.portfolio_risk.

    Theory: docs/background/19-portfolio-risk.md
    - max_cvar_pct: Historical CVaR upper bound (§4.1 FRTB α=0.975)
    - max_corr_avg: average pairwise correlation upper bound
    - min_enb_ratio: ENB / N lower bound (§7: ENB >= 0.3·N guideline)
    - cvar_levels: per-alpha CVaR level specs; evaluated after max_cvar_pct (independent, first-violation-wins)

    on_*_breach defaults chosen by rule semantics:
    - cvar proportional to order size → REDUCE can actually shrink cvar
    - correlation is a portfolio STATE → single new order cannot improve it → BLOCK
    - ENB breach = structural diversification failure → HALT for human rebalance
    """
    max_cvar_pct: Optional[PositiveFloat] = Field(
        default=None, lt=1.0,
        description="Max portfolio CVaR (positive loss fraction). Cite: 19-portfolio-risk.md §4.1",
    )
    max_corr_avg: Optional[float] = Field(
        default=None, ge=-1.0, le=1.0,
        description="Max allowed average pairwise correlation.",
    )
    min_enb_ratio: Optional[PositiveFloat] = Field(
        default=None, le=1.0,
        description="Min ENB/N ratio. Guideline: 19-portfolio-risk.md §7 (ENB >= 0.3·N).",
    )
    alpha: Optional[float] = Field(
        default=0.975, gt=0.0, lt=1.0,
        description="CVaR/VaR alpha. Default 0.975 cites §4.1 Basel III FRTB.",
    )
    cvar_levels: Optional[list[tuple[float, str]]] = Field(
        default=None,
        description="Per-alpha CVaR level list [(alpha, label), ...]. "
                    "Evaluated after max_cvar_pct, first-violation-wins. "
                    "Breach threshold uses max_cvar_pct per level from snap.cvar_levels.",
    )
    # extreme_fear_block: block new buy orders when fear_greed_proxy < extreme_fear_threshold.
    # Price-only signal; social/macro data intentionally excluded (patent-avoidance).
    extreme_fear_block: Optional[bool] = None
    extreme_fear_threshold: Optional[float] = Field(default=0.2, ge=0.0, le=1.0)
    on_cvar_breach: Action = Action.REDUCE
    on_corr_breach: Action = Action.BLOCK
    on_enb_breach: Action = Action.HALT


class PerPosition(_Strict):
    max_weight_pct: Optional[PositiveFloat] = Field(default=None, le=100.0)
    max_qty: Optional[PositiveInt] = None


class SectorLimit(_Strict):
    sector: str
    max_weight_pct: PositiveFloat = Field(le=100.0)


class Drawdown(_Strict):
    max_intraday_dd_pct: Optional[PositiveFloat] = None
    max_running_dd_pct: Optional[PositiveFloat] = None
    on_breach: Action = Action.HALT


class Policy(_Strict):
    policy_version: PositiveInt
    name: str = Field(min_length=1)
    description: Optional[str] = None
    per_trade: Optional[PerTrade] = None
    per_day: Optional[PerDay] = None
    per_portfolio: Optional[PerPortfolio] = None
    per_portfolio_risk: Optional[PerPortfolioRisk] = None
    per_position: Optional[PerPosition] = None
    sector_limits: list[SectorLimit] = Field(default_factory=list)
    drawdown: Optional[Drawdown] = None


# ---------- runtime snapshot ----------

class Order(_Strict):
    symbol: str
    side: str               # 'buy' | 'sell'
    qty: float
    price: float
    sector: Optional[str] = None

    @property
    def notional(self) -> float:
        return self.qty * self.price


class Snapshot(_Strict):
    """Inputs to the evaluator at decision time."""
    intent: Order
    equity_krw: float
    gross_exposure_krw: float = 0.0
    net_exposure_krw: float = 0.0
    position_qty: float = 0.0
    position_weight_pct: float = 0.0
    sector_weights_pct: dict[str, float] = Field(default_factory=dict)
    day_orders: int = 0
    day_realized_pnl_krw: float = 0.0       # negative = loss
    day_turnover_krw: float = 0.0
    intraday_dd_pct: float = 0.0            # positive number, % drop from intraday peak
    running_dd_pct: float = 0.0
    portfolio_risk: Optional[PortfolioRiskReport] = None  # injected by periodic evaluator
    # Price-based fear/greed proxy: current_price / rolling_max(window).
    # Intentionally excludes social-sentiment and macro data (patent-avoidance).
    fear_greed_proxy: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class Decision(_Strict):
    action: Action
    rule_id: Optional[str] = None
    message: Optional[str] = None


# ---------- loader ----------

def load_policy(path: str | Path) -> Policy:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"policy file must be a mapping: {path}")
    return Policy.model_validate(raw)


# ---------- evaluator ----------

def _block(rule_id: str, msg: str) -> Decision:
    return Decision(action=Action.BLOCK, rule_id=rule_id, message=msg)


def evaluate(policy: Policy, snap: Snapshot) -> Decision:
    """Evaluate snapshot against policy. Returns first violation, else ALLOW."""
    intent = snap.intent

    # per_trade
    pt = policy.per_trade
    if pt:
        if pt.max_notional_krw is not None and intent.notional > pt.max_notional_krw:
            return _block("per_trade.max_notional_krw",
                          f"notional {intent.notional} > {pt.max_notional_krw}")
        if pt.max_qty is not None and intent.qty > pt.max_qty:
            return _block("per_trade.max_qty",
                          f"qty {intent.qty} > {pt.max_qty}")
        if pt.allowed_sides is not None and intent.side not in pt.allowed_sides:
            return _block("per_trade.allowed_sides",
                          f"side {intent.side!r} not in {pt.allowed_sides}")

    # per_day
    pd = policy.per_day
    if pd:
        if pd.max_orders is not None and snap.day_orders + 1 > pd.max_orders:
            return _block("per_day.max_orders",
                          f"orders {snap.day_orders + 1} > {pd.max_orders}")
        if pd.max_loss_krw is not None and -snap.day_realized_pnl_krw > pd.max_loss_krw:
            return _block("per_day.max_loss_krw",
                          f"loss {-snap.day_realized_pnl_krw} > {pd.max_loss_krw}")
        if pd.max_turnover_krw is not None and (
            snap.day_turnover_krw + intent.notional > pd.max_turnover_krw
        ):
            return _block("per_day.max_turnover_krw",
                          "turnover would exceed limit")

    # per_portfolio
    pp = policy.per_portfolio
    if pp:
        new_gross = snap.gross_exposure_krw + intent.notional
        if pp.max_gross_exposure_krw is not None and new_gross > pp.max_gross_exposure_krw:
            return _block("per_portfolio.max_gross_exposure_krw",
                          f"gross {new_gross} > {pp.max_gross_exposure_krw}")
        if pp.max_leverage is not None and snap.equity_krw > 0:
            lev = new_gross / snap.equity_krw
            if lev > pp.max_leverage:
                return _block("per_portfolio.max_leverage",
                              f"leverage {lev:.2f} > {pp.max_leverage}")

    # per_portfolio_risk (periodic-report-gated; no cost when report absent)
    ppr = policy.per_portfolio_risk
    rep = snap.portfolio_risk
    if ppr is not None and rep is not None:
        # max_cvar_pct checked first (independent of cvar_levels)
        if ppr.max_cvar_pct is not None and rep.cvar_pct > ppr.max_cvar_pct:
            return Decision(action=ppr.on_cvar_breach,
                            rule_id="per_portfolio_risk.max_cvar_pct",
                            message=f"cvar {rep.cvar_pct:.4f} > {ppr.max_cvar_pct:.4f}")
        # cvar_levels: sequential per-level check, first-violation-wins
        # Breach threshold is ppr.max_cvar_pct applied to each level's cvar_pct in snap.
        if (ppr.cvar_levels is not None and ppr.max_cvar_pct is not None
                and rep.cvar_levels is not None):
            for _alpha, label in ppr.cvar_levels:
                level_entry = rep.cvar_levels.get(label)
                if level_entry is None:
                    continue
                level_cvar = level_entry.get("cvar_pct", 0.0)
                if level_cvar > ppr.max_cvar_pct:
                    return Decision(
                        action=ppr.on_cvar_breach,
                        rule_id=f"per_portfolio_risk.cvar_levels.{label}",
                        message=f"cvar_levels[{label}] {level_cvar:.4f} > {ppr.max_cvar_pct:.4f}",
                    )
        if ppr.max_corr_avg is not None and rep.corr_avg > ppr.max_corr_avg:
            return Decision(action=ppr.on_corr_breach,
                            rule_id="per_portfolio_risk.max_corr_avg",
                            message=f"corr_avg {rep.corr_avg:.3f} > {ppr.max_corr_avg:.3f}")
        if ppr.min_enb_ratio is not None and rep.enb_ratio < ppr.min_enb_ratio:
            return Decision(action=ppr.on_enb_breach,
                            rule_id="per_portfolio_risk.min_enb_ratio",
                            message=f"enb_ratio {rep.enb_ratio:.3f} < {ppr.min_enb_ratio:.3f}")

    # extreme_fear_block: price-based fear proxy gate, independent of portfolio_risk report.
    # Blocks new buy orders only; sell orders are not gated (avoids forced liquidation during panic).
    if ppr is not None and ppr.extreme_fear_block:
        threshold = ppr.extreme_fear_threshold if ppr.extreme_fear_threshold is not None else 0.2
        if snap.fear_greed_proxy is not None and snap.fear_greed_proxy < threshold:
            if intent.side == "buy":
                return _block(
                    "per_portfolio_risk.extreme_fear_block",
                    f"fear_greed_proxy {snap.fear_greed_proxy:.3f} < {threshold:.3f} (buy blocked)",
                )

    # per_position
    pos = policy.per_position
    if pos:
        if pos.max_qty is not None and snap.position_qty + intent.qty > pos.max_qty:
            return _block("per_position.max_qty",
                          "position qty would exceed limit")
        if pos.max_weight_pct is not None and snap.position_weight_pct > pos.max_weight_pct:
            return _block("per_position.max_weight_pct",
                          f"weight {snap.position_weight_pct} > {pos.max_weight_pct}")

    # sector
    for sl in policy.sector_limits:
        w = snap.sector_weights_pct.get(sl.sector, 0.0)
        if w > sl.max_weight_pct:
            return _block(f"sector_limits.{sl.sector}",
                          f"sector weight {w} > {sl.max_weight_pct}")

    # drawdown
    dd = policy.drawdown
    if dd:
        if dd.max_intraday_dd_pct is not None and snap.intraday_dd_pct > dd.max_intraday_dd_pct:
            return Decision(action=dd.on_breach,
                            rule_id="drawdown.max_intraday_dd_pct",
                            message=f"intraday dd {snap.intraday_dd_pct} > {dd.max_intraday_dd_pct}")
        if dd.max_running_dd_pct is not None and snap.running_dd_pct > dd.max_running_dd_pct:
            return Decision(action=dd.on_breach,
                            rule_id="drawdown.max_running_dd_pct",
                            message=f"running dd {snap.running_dd_pct} > {dd.max_running_dd_pct}")

    return Decision(action=Action.ALLOW)
