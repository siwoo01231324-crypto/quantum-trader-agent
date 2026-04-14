"""Risk rule DSL — pydantic schema, YAML loader, evaluation function.

Stub implementation: covers per_trade / per_day / per_portfolio / per_position /
sector_limits / drawdown. Returns first-violation Decision.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt


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
