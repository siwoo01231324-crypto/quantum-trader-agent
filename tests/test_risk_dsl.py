"""Tests for the risk rule DSL."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from risk.dsl import (  # noqa: E402
    Policy, PerTrade, PerDay, PerPortfolio, PerPosition,
    Drawdown, Snapshot, Order, Action, load_policy, evaluate,
)


POLICIES = ROOT / "policies"


# ---------- loader / schema validation ----------

def test_load_all_policy_files():
    for name in ("conservative", "neutral", "aggressive"):
        p = load_policy(POLICIES / f"{name}.yaml")
        assert p.name == name
        assert p.policy_version == 1


def test_invalid_yaml_missing_required(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("description: only a description\n", encoding="utf-8")
    with pytest.raises(Exception):
        load_policy(bad)


def test_invalid_yaml_extra_field(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "policy_version: 1\nname: x\nunknown_field: 123\n", encoding="utf-8"
    )
    with pytest.raises(Exception):
        load_policy(bad)


def test_invalid_yaml_bad_type(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "policy_version: -1\nname: x\n", encoding="utf-8"
    )
    with pytest.raises(Exception):
        load_policy(bad)


# ---------- breach detection ----------

def _policy() -> Policy:
    return Policy(
        policy_version=1, name="t",
        per_trade=PerTrade(max_notional_krw=1_000_000, max_qty=100,
                           allowed_sides=["buy"]),
        per_day=PerDay(max_orders=10, max_loss_krw=500_000,
                       max_turnover_krw=10_000_000),
        per_portfolio=PerPortfolio(max_gross_exposure_krw=50_000_000,
                                   max_leverage=2.0),
        per_position=PerPosition(max_weight_pct=10.0, max_qty=500),
        drawdown=Drawdown(max_intraday_dd_pct=2.0, on_breach=Action.HALT),
    )


def _snap(**over) -> Snapshot:
    base = dict(
        intent=Order(symbol="A", side="buy", qty=10, price=10_000),
        equity_krw=10_000_000,
    )
    base.update(over)
    return Snapshot(**base)


def test_allow_when_within_limits():
    d = evaluate(_policy(), _snap())
    assert d.action == Action.ALLOW


def test_block_per_trade_notional():
    d = evaluate(_policy(),
                 _snap(intent=Order(symbol="A", side="buy",
                                    qty=200, price=10_000)))
    assert d.action == Action.BLOCK
    assert d.rule_id == "per_trade.max_notional_krw"


def test_block_per_trade_side():
    d = evaluate(_policy(),
                 _snap(intent=Order(symbol="A", side="sell",
                                    qty=10, price=10_000)))
    assert d.action == Action.BLOCK
    assert d.rule_id == "per_trade.allowed_sides"


def test_block_per_day_loss():
    d = evaluate(_policy(), _snap(day_realized_pnl_krw=-600_000))
    assert d.action == Action.BLOCK
    assert d.rule_id == "per_day.max_loss_krw"


def test_block_per_day_orders():
    d = evaluate(_policy(), _snap(day_orders=10))
    assert d.action == Action.BLOCK
    assert d.rule_id == "per_day.max_orders"


def test_block_leverage():
    d = evaluate(_policy(),
                 _snap(equity_krw=1_000_000,
                       gross_exposure_krw=2_000_000,
                       intent=Order(symbol="A", side="buy",
                                    qty=100, price=10_000)))
    assert d.action == Action.BLOCK
    assert d.rule_id == "per_portfolio.max_leverage"


def test_drawdown_halt():
    d = evaluate(_policy(), _snap(intraday_dd_pct=2.5))
    assert d.action == Action.HALT
    assert d.rule_id == "drawdown.max_intraday_dd_pct"


def test_sector_limit_breach():
    p = Policy.model_validate({
        "policy_version": 1, "name": "s",
        "sector_limits": [{"sector": "tech", "max_weight_pct": 20.0}],
    })
    d = evaluate(p, _snap(sector_weights_pct={"tech": 25.0}))
    assert d.action == Action.BLOCK
    assert d.rule_id == "sector_limits.tech"
