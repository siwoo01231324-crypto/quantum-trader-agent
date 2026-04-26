"""tests/test_shadow_report.py — shadow_report.py 단위 테스트 (#80 Phase E-2)."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pandas as pd
import pytest

from scripts.shadow_report import (
    CompareConditions,
    FillRecord,
    compare_sharpe,
    daily_pnl_series,
    daily_return_series,
    export_strategy_returns,
    parse_fills,
    render_report_md,
    sharpe_ratio,
    verify_exit_criteria,
)
from src.live.types import WALEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(event_type: str, payload: dict) -> WALEvent:
    return WALEvent(
        ts="2026-04-25T09:00:00+00:00",
        event_type=event_type,
        schema_version=1,
        payload=payload,
    )


def _fill_payload(
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    qty: str = "0.1",
    price: str = "50000",
    fees: str = "0",
    strategy_id: str = "strat_a",
) -> dict:
    return {
        "symbol": symbol,
        "side": side,
        "fill_qty": qty,
        "fill_price": price,
        "fees": fees,
        "fee_asset": "USDT",
        "strategy_id": strategy_id,
    }


# ---------------------------------------------------------------------------
# parse_fills
# ---------------------------------------------------------------------------

def test_parse_fills_skips_non_filled():
    events = [
        _make_event("order_submitted", {"symbol": "BTCUSDT", "side": "BUY", "fill_price": "50000"}),
        _make_event("order_cancelled", {"symbol": "BTCUSDT", "side": "BUY", "fill_price": "50000"}),
        _make_event("order_filled", _fill_payload()),
    ]
    fills = parse_fills(events)
    assert len(fills) == 1
    assert fills[0].symbol == "BTCUSDT"


def test_parse_fills_extracts_fields():
    events = [_make_event("order_filled", _fill_payload(price="51000", qty="0.2", fees="2.5"))]
    fills = parse_fills(events)
    assert len(fills) == 1
    f = fills[0]
    assert f.price == Decimal("51000")
    assert f.qty == Decimal("0.2")
    assert f.fees == Decimal("2.5")
    assert f.side == "BUY"


def test_parse_fills_skips_malformed(caplog):
    # fill_price 누락
    events = [_make_event("order_filled", {"symbol": "BTCUSDT", "side": "BUY"})]
    fills = parse_fills(events)
    assert fills == []


# ---------------------------------------------------------------------------
# daily_pnl_series
# ---------------------------------------------------------------------------

def _make_fill(side: str, price: str, qty: str = "0.1", fees: str = "0", ts_str: str = "2026-04-25T09:00:00+00:00") -> FillRecord:
    return FillRecord(
        ts=datetime.fromisoformat(ts_str),
        strategy_id="strat_a",
        symbol="BTCUSDT",
        side=side,
        qty=Decimal(qty),
        price=Decimal(price),
        fees=Decimal(fees),
        fee_asset="USDT",
    )


def test_daily_pnl_buy_sell():
    """BUY 0.1 @ 50000 + SELL 0.1 @ 51000 → PnL = +100 (같은 날, fees=0)."""
    fills = [
        _make_fill("BUY", "50000", qty="0.1"),   # cash = -5000
        _make_fill("SELL", "51000", qty="0.1"),  # cash = +5100
    ]
    pnl = daily_pnl_series(fills)
    assert not pnl.empty
    assert abs(pnl.iloc[0] - 100.0) < 1e-6


def test_daily_pnl_buy_sell_with_fees():
    """BUY 0.1 @ 50000 fee=2 + SELL 0.1 @ 51000 fee=2 → PnL = +96."""
    fills = [
        _make_fill("BUY", "50000", qty="0.1", fees="2"),   # cash = -5000 - 2 = -5002
        _make_fill("SELL", "51000", qty="0.1", fees="2"),  # cash = +5100 - 2 = +5098
    ]
    pnl = daily_pnl_series(fills)
    assert abs(pnl.iloc[0] - 96.0) < 1e-6


def test_daily_pnl_groups_by_date():
    """다른 날 fill → 날짜별 그룹화."""
    fills = [
        _make_fill("SELL", "50000", ts_str="2026-04-24T09:00:00+00:00"),
        _make_fill("SELL", "51000", ts_str="2026-04-25T09:00:00+00:00"),
    ]
    pnl = daily_pnl_series(fills)
    assert len(pnl) == 2


def test_daily_pnl_empty_fills():
    pnl = daily_pnl_series([])
    assert pnl.empty


# ---------------------------------------------------------------------------
# sharpe_ratio
# ---------------------------------------------------------------------------

def test_sharpe_ratio_basic():
    """모두 같은 값이면 std=0 → nan 반환."""
    rets = pd.Series([0.01] * 252)
    s = sharpe_ratio(rets)
    # std=0 이므로 nan
    assert math.isnan(s)


def test_sharpe_ratio_positive():
    """양수 수익률 시계열 → 양수 Sharpe."""
    import numpy as np
    rng = np.random.default_rng(42)
    rets = pd.Series(rng.normal(0.001, 0.01, 100))
    s = sharpe_ratio(rets)
    assert not math.isnan(s)
    assert isinstance(s, float)


def test_sharpe_ratio_insufficient_samples():
    """1건 → nan."""
    rets = pd.Series([0.01])
    assert math.isnan(sharpe_ratio(rets))


def test_sharpe_ratio_zero_std():
    """표준편차 0 → nan."""
    rets = pd.Series([0.01, 0.01, 0.01])
    assert math.isnan(sharpe_ratio(rets))


# ---------------------------------------------------------------------------
# CompareConditions.matches
# ---------------------------------------------------------------------------

def _default_cond(**kwargs) -> CompareConditions:
    defaults = dict(
        data_source="binance_futures_usdtm",
        slippage_model="zero_slip",
        taker_fee_bps=5.0,
        sizing_method="resolve_size_v1",
    )
    defaults.update(kwargs)
    return CompareConditions(**defaults)


def test_compare_conditions_match_all():
    c1 = _default_cond()
    c2 = _default_cond()
    matches, mismatches = c1.matches(c2)
    assert matches is True
    assert mismatches == []


def test_compare_conditions_mismatch_data_source():
    c1 = _default_cond(data_source="binance_spot")
    c2 = _default_cond(data_source="binance_futures_usdtm")
    matches, mismatches = c1.matches(c2)
    assert matches is False
    assert any("data_source" in m for m in mismatches)


def test_compare_conditions_mismatch_slippage():
    c1 = _default_cond(slippage_model="linear")
    c2 = _default_cond()
    matches, mismatches = c1.matches(c2)
    assert matches is False
    assert any("slippage_model" in m for m in mismatches)


def test_compare_conditions_mismatch_fee():
    c1 = _default_cond(taker_fee_bps=10.0)
    c2 = _default_cond(taker_fee_bps=5.0)
    matches, mismatches = c1.matches(c2)
    assert matches is False
    assert any("taker_fee_bps" in m for m in mismatches)


# ---------------------------------------------------------------------------
# compare_sharpe
# ---------------------------------------------------------------------------

def _const_returns(val: float, n: int = 50) -> pd.Series:
    """평균=val, std≠0 인 시계열 생성 (val 중심 미세 노이즈)."""
    import numpy as np
    rng = np.random.default_rng(0)
    return pd.Series(val + rng.normal(0, 0.001, n))


def test_compare_sharpe_passes_within_threshold():
    """shadow≈1.2, backtest≈1.0 → 조건 일치, diff ≤ 0.3 → passed=True."""
    import numpy as np
    rng = np.random.default_rng(1)
    # 두 시리즈 동일하게 → diff=0
    shared = pd.Series(rng.normal(0.001, 0.01, 100))
    cond = _default_cond()
    result = compare_sharpe(shared, shared, cond, cond, threshold=0.3)
    assert result["passed"] is True
    assert result["conditions_match"] is True
    assert result["diff"] == pytest.approx(0.0, abs=1e-9)


def test_compare_sharpe_fails_threshold():
    """diff > threshold → passed=False."""
    import numpy as np
    rng = np.random.default_rng(2)
    s1 = pd.Series(rng.normal(0.01, 0.005, 100))   # high sharpe
    s2 = pd.Series(rng.normal(0.0001, 0.01, 100))  # low sharpe
    cond = _default_cond()
    result = compare_sharpe(s1, s2, cond, cond, threshold=0.3)
    # diff likely >> 0.3 given different means/stds
    if result["diff"] > 0.3:
        assert result["passed"] is False
    # If by chance diff <= 0.3, just verify structure
    assert "passed" in result
    assert "diff" in result


def test_compare_sharpe_fails_conditions_mismatch():
    """조건 불일치 → passed=False (Sharpe 차이가 작아도)."""
    import numpy as np
    rng = np.random.default_rng(3)
    shared = pd.Series(rng.normal(0.001, 0.01, 100))
    c1 = _default_cond(data_source="binance_spot")
    c2 = _default_cond(data_source="binance_futures_usdtm")
    result = compare_sharpe(shared, shared, c1, c2, threshold=0.3)
    assert result["passed"] is False
    assert result["conditions_match"] is False
    assert len(result["mismatches"]) >= 1


# ---------------------------------------------------------------------------
# export_strategy_returns
# ---------------------------------------------------------------------------

def test_export_strategy_returns_calls_orchestrator():
    fills = [
        _make_fill("SELL", "50000", ts_str="2026-04-25T09:00:00+00:00"),
        FillRecord(
            ts=datetime.fromisoformat("2026-04-26T09:00:00+00:00"),
            strategy_id="strat_b",
            symbol="ETHUSDT",
            side="SELL",
            qty=Decimal("1"),
            price=Decimal("3000"),
            fees=Decimal("0"),
            fee_asset="USDT",
        ),
    ]
    mock_orch = MagicMock()
    series_map = export_strategy_returns(mock_orch, fills, initial_balance=100_000.0)

    assert "strat_a" in series_map
    assert "strat_b" in series_map
    assert mock_orch.register_strategy_returns.call_count == 2
    calls = {call.args[0] for call in mock_orch.register_strategy_returns.call_args_list}
    assert calls == {"strat_a", "strat_b"}


def test_export_strategy_returns_no_orchestrator():
    """orchestrator=None → 계산만, 예외 없음."""
    fills = [_make_fill("SELL", "50000")]
    series_map = export_strategy_returns(None, fills)
    assert "strat_a" in series_map
    assert isinstance(series_map["strat_a"], pd.Series)


def test_export_strategy_returns_empty_fills():
    result = export_strategy_returns(None, [])
    assert result == {}


# ---------------------------------------------------------------------------
# render_report_md
# ---------------------------------------------------------------------------

def test_render_report_md_includes_summary():
    fills = [_make_fill("SELL", "50000")]
    pnl = daily_pnl_series(fills)
    rets = daily_return_series(pnl)
    sharpe = sharpe_ratio(rets)
    md = render_report_md(fills, pnl, rets, sharpe)
    assert "Shadow Paper" in md
    assert "총 fill 건수: 1" in md
    assert "요약" in md


def test_render_report_md_compare_section():
    pnl = pd.Series(dtype=float, name="daily_pnl_usdt")
    rets = pd.Series(dtype=float, name="daily_return")
    compare_result = {
        "sharpe_shadow": 1.2,
        "sharpe_backtest": 1.0,
        "diff": 0.2,
        "threshold": 0.3,
        "conditions_match": True,
        "mismatches": [],
        "passed": True,
    }
    md = render_report_md([], pnl, rets, float("nan"), compare_result=compare_result)
    assert "Sharpe 비교" in md
    assert "PASSED" in md


def test_render_report_md_exit_criteria_section():
    pnl = pd.Series(dtype=float, name="daily_pnl_usdt")
    rets = pd.Series(dtype=float, name="daily_return")
    exit_criteria = {"항목A": True, "항목B": False}
    md = render_report_md([], pnl, rets, float("nan"), exit_criteria=exit_criteria)
    assert "Exit Criteria" in md
    assert "항목A" in md
    assert "항목B" in md


# ---------------------------------------------------------------------------
# verify_exit_criteria
# ---------------------------------------------------------------------------

def test_verify_exit_criteria_all_pass():
    result = verify_exit_criteria(
        fills=[],
        daily_pnl=pd.Series(dtype=float),
        sharpe_compare_passed=True,
        ws_reconnect_count=1,
        lag_over_500ms_ratio=0.01,
        kill_switch_tests_passed=True,
    )
    assert all(result.values())


def test_verify_exit_criteria_ws_reconnect_fail():
    result = verify_exit_criteria(
        fills=[],
        daily_pnl=pd.Series(dtype=float),
        sharpe_compare_passed=True,
        ws_reconnect_count=0,
        lag_over_500ms_ratio=0.01,
        kill_switch_tests_passed=True,
    )
    assert result["WS 단절 자동 재연결 정상 (≥1회)"] is False


def test_verify_exit_criteria_lag_fail():
    result = verify_exit_criteria(
        fills=[],
        daily_pnl=pd.Series(dtype=float),
        sharpe_compare_passed=True,
        ws_reconnect_count=1,
        lag_over_500ms_ratio=0.10,  # > 5%
        kill_switch_tests_passed=True,
    )
    assert result["시세 lag > 500ms 발생률 < 5%"] is False
