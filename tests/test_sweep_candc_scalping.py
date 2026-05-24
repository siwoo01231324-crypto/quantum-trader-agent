"""sweep_candc_scalping driver — unit safety guards (2026-05-21).

핵심 회귀 가드:
1. _safe_float NaN → None (JSON allow_nan=False 호환)
2. _edge() PF/expectancy 계산 정확성 (trades=0 edge case 포함)
3. _combos() 8 combos per-strategy, 32 total cardinality
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "sweep_candc_scalping", _REPO / "scripts" / "sweep_candc_scalping.py",
)
sweep = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sweep)


class TestSafeFloat:
    def test_nan_to_none(self):
        assert sweep._safe_float(float("nan")) is None

    def test_finite_passes_through(self):
        assert sweep._safe_float(0.5) == 0.5
        assert sweep._safe_float(-0.123) == -0.123
        assert sweep._safe_float(0.0) == 0.0

    def test_none_input(self):
        assert sweep._safe_float(None) is None

    def test_string_invalid(self):
        assert sweep._safe_float("abc") is None

    def test_string_numeric(self):
        assert sweep._safe_float("1.5") == 1.5


class TestEdge:
    def test_zero_trades_safe(self):
        """trades=0 edge case — division by zero 회피, expectancy=0."""
        m = {"trades": 0, "win_rate": 0.0, "realized_pnl_profit": 0.0,
             "realized_pnl_loss": 0.0, "sharpe": 0.0, "mdd": 0.0, "ann_return": 0.0}
        e = sweep._edge(m)
        assert e["trades"] == 0
        assert e["expectancy"] == 0.0
        assert e["avg_win"] == 0.0
        assert e["avg_loss"] == 0.0
        # PF: L=0 → infinity. payoff: avg_l=0 → infinity. JSON 안전위해 inf 만 봐.
        assert e["profit_factor"] == float("inf") or e["profit_factor"] is None

    def test_positive_edge_pf_above_one(self):
        # 100 trades, 40% win, P=4.0, L=-2.0 → PF=2.0, exp=+0.02
        m = {"trades": 100, "win_rate": 0.4,
             "realized_pnl_profit": 4.0, "realized_pnl_loss": -2.0,
             "sharpe": 1.5, "mdd": -0.2, "ann_return": 0.3}
        e = sweep._edge(m)
        assert e["profit_factor"] == pytest.approx(2.0, abs=1e-9)
        assert e["expectancy"] == pytest.approx(0.02, abs=1e-9)
        assert e["avg_win"] == pytest.approx(4.0 / 40, abs=1e-9)
        assert e["avg_loss"] == pytest.approx(-2.0 / 60, abs=1e-9)
        # All values JSON-serializable (no NaN/inf in finite case)
        for v in e.values():
            if isinstance(v, float):
                assert math.isfinite(v) or v is None

    def test_net_loser_pf_below_one(self):
        # The previously-confirmed live-scanner net-loser shape
        m = {"trades": 50000, "win_rate": 0.343,
             "realized_pnl_profit": 800.0, "realized_pnl_loss": -920.0,
             "sharpe": 3.19, "mdd": -0.355, "ann_return": 2.6}
        e = sweep._edge(m)
        assert e["profit_factor"] is not None and e["profit_factor"] < 1.0
        assert e["expectancy"] is not None and e["expectancy"] < 0
        # 이게 정확히 gate 가 잡아야 할 케이스 — Sharpe 3.19 인데 PF<1, exp<0.


class TestCombos:
    def test_eight_combos_per_strategy(self):
        combos = sweep._combos()
        assert len(combos) == 8   # 2 sl × 2 tp × 2 trail

    def test_combo_field_shape(self):
        combos = sweep._combos()
        for c in combos:
            assert "stop_loss_pct" in c
            assert "take_profit_pct" in c
            assert "trailing_stop_pct" in c
            assert "_tag" in c
            assert c["stop_loss_pct"] in sweep.GRID_STOP_LOSS
            assert c["take_profit_pct"] in sweep.GRID_TAKE_PROFIT
            assert c["trailing_stop_pct"] in sweep.GRID_TRAILING

    def test_strategy_list_has_four_cand_c(self):
        assert len(sweep.CAND_C_STRATEGIES) == 4
        for sid in sweep.CAND_C_STRATEGIES:
            assert sid.startswith("live_")
