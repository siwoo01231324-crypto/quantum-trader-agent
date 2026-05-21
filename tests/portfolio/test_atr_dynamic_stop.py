"""ATR-based 동적 stop/TP/trailing override 회귀 테스트 (2026-05-21).

NEARUSDT 1.7500 매수 → 1.7490 매도 (-0.057%) churn 사례 fix. 정적
stop_loss_pct=0.005 / trailing_stop_pct=0.005 가 코인 정상 노이즈 안에 있어
매 진입이 stop/trailing 으로 잡히던 문제. Strategy 가 진입 시점 ATR 로
동적 거리를 계산해 Signal 에 override 로 실어보내고, risk manager 가
per-(sid, symbol) dynamic policy 로 적용.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from datetime import datetime, timezone
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_breakout_with_atr_stop import (
    LiveBreakoutWithAtrStop, _calculate_atr,
)
from portfolio import AsyncStrategyOrchestrator
from portfolio.live_position_risk import (
    LivePositionRiskManager, StopTpPolicy,
)
from live.pnl_aggregator import PnLAggregator
from live.strategy_position_store import StrategyPositionStore
from risk.dsl import Policy


# ── ATR 계산 unit ────────────────────────────────────────────────

def test_atr_returns_none_on_short_history():
    df = pd.DataFrame({"high": [1, 2], "low": [0, 1], "close": [1, 1]})
    assert _calculate_atr(df, period=14) is None


def test_atr_simple_constant_range():
    """매 bar high-low=2, prev_close 와도 2 차이 → ATR=2 (constant range)."""
    n = 20
    df = pd.DataFrame({
        "high": np.full(n, 10.0),
        "low": np.full(n, 8.0),
        "close": np.full(n, 9.0),
    })
    atr = _calculate_atr(df, period=14)
    assert atr == pytest.approx(2.0, rel=1e-9)


# ── Strategy 가 Signal 에 override 실어보내는지 ─────────────────

def _breakout_history(n: int = 30, base: float = 100.0) -> pd.DataFrame:
    """20봉 high breakout 조건을 마지막 봉이 만족하도록 만든 history."""
    close = np.linspace(base, base + 5.0, n)  # 단조 증가 → 마지막이 max
    high = close * 1.005
    low = close * 0.995
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close,
         "volume": np.full(n, 1000.0)},
        index=pd.date_range("2026-01-01", periods=n, freq="1min"),
    )


def _hold_history(n: int = 30, base: float = 100.0) -> pd.DataFrame:
    """20봉 high 보다 마지막이 낮은 = no_breakout 케이스."""
    close = np.linspace(base + 5.0, base, n)  # 단조 감소 → 마지막이 min
    high = close * 1.005
    low = close * 0.995
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close,
         "volume": np.full(n, 1000.0)},
        index=pd.date_range("2026-01-01", periods=n, freq="1min"),
    )


def test_strategy_no_atr_mult_default_no_override():
    """mult 미설정 시 (기존 default) Signal 에 override 모두 None."""
    strat = LiveBreakoutWithAtrStop()
    ctx = {"market_snapshot": {"history": _breakout_history()}}
    sig = asyncio.run(strat.on_bar(ctx))
    assert sig.action == "buy"
    assert sig.stop_loss_pct_override is None
    assert sig.take_profit_pct_override is None
    assert sig.trailing_stop_pct_override is None


def test_strategy_with_atr_mult_emits_override():
    """stop_atr_mult / trailing_stop_atr_mult 설정 시 override pct 채워짐."""
    strat = LiveBreakoutWithAtrStop(
        stop_atr_mult=1.5,
        take_profit_atr_mult=3.0,
        trailing_stop_atr_mult=1.5,
        atr_period=14,
    )
    history = _breakout_history()
    ctx = {"market_snapshot": {"history": history}}
    sig = asyncio.run(strat.on_bar(ctx))

    assert sig.action == "buy"
    # ATR(14) on this synthetic history > 0 → override 가 합리적 범위 (0, 1) 안.
    assert sig.stop_loss_pct_override is not None
    assert 0 < sig.stop_loss_pct_override < 1
    assert sig.take_profit_pct_override is not None
    assert sig.take_profit_pct_override > sig.stop_loss_pct_override  # 3× vs 1.5×
    assert sig.trailing_stop_pct_override is not None
    # 직접 검산: ATR × 1.5 / last_close
    atr = _calculate_atr(history, period=14)
    last_close = float(history["close"].iloc[-1])
    expected = min(0.999, atr * 1.5 / last_close)
    assert sig.stop_loss_pct_override == pytest.approx(expected, rel=1e-9)


def test_strategy_hold_signal_does_not_compute_atr():
    """no_breakout 시 BUY 안 나옴 → override 도 무의미 (Signal 자체 hold)."""
    strat = LiveBreakoutWithAtrStop(stop_atr_mult=1.5)
    ctx = {"market_snapshot": {"history": _hold_history()}}
    sig = asyncio.run(strat.on_bar(ctx))
    assert sig.action == "hold"


def test_strategy_invalid_mult_raises():
    with pytest.raises(ValueError, match="stop_atr_mult"):
        LiveBreakoutWithAtrStop(stop_atr_mult=0.0)
    with pytest.raises(ValueError, match="atr_period"):
        LiveBreakoutWithAtrStop(atr_period=1)


# ── Risk manager dynamic policy override ─────────────────────────

def _risk_mgr() -> LivePositionRiskManager:
    return LivePositionRiskManager(
        position_store=StrategyPositionStore(),
        pnl_aggregator=PnLAggregator(),
    )


def test_register_entry_override_skipped_without_static_policy():
    """정적 policy 미등록 sid 에 override 호출 → no-op."""
    mgr = _risk_mgr()
    mgr.register_entry_override(
        "unknown", "NEARUSDT", stop_loss_pct=0.02,
    )
    assert mgr._dynamic_policies == {}


def test_register_entry_override_uses_static_for_missing_fields():
    """일부 필드만 override 시 나머지는 정적 policy 에서 가져옴."""
    mgr = _risk_mgr()
    mgr.register_strategy_policy(
        "scan", stop_loss_pct=0.005, take_profit_pct=0.01, trailing_stop_pct=0.005,
    )
    mgr.register_entry_override(
        "scan", "NEARUSDT", stop_loss_pct=0.02,  # stop 만 override
    )
    dyn = mgr._dynamic_policies[("scan", "NEARUSDT")]
    assert dyn.stop_loss_pct == 0.02
    assert dyn.take_profit_pct == 0.01      # 정적 fallback
    assert dyn.trailing_stop_pct == 0.005   # 정적 fallback


def test_evaluate_uses_dynamic_override_when_present():
    """Dynamic override 가 등록되어 있으면 정적 policy 대신 그것으로 평가."""
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    mgr = LivePositionRiskManager(position_store=store, pnl_aggregator=pnl)

    # 정적 stop=0.005 (=0.5%) — 1.7490 가격이면 stop 발사돼야 (1.75 entry, -0.057%)
    # 가 아닌 — 더 강한 trigger 가 필요. 시나리오: 정적은 -0.5% 라 1.7490 에서
    # 발사 안 됨. Dynamic override 가 stop=0.02 (-2%) 면 발사 안 되어야 함.
    mgr.register_strategy_policy(
        "scan", stop_loss_pct=0.005, take_profit_pct=0.01,
    )
    # 진입 = qty +135, avg=1.75
    store.record_fill(strategy_id="scan", symbol="NEARUSDT", side="buy", qty=Decimal("135"))
    pnl.record_fill(
        strategy_id="scan", symbol="NEARUSDT", side="buy",
        qty=Decimal("135"), price=Decimal("1.75"),
    )

    # 정적 policy 로는 1.7400 (= -0.57%) 에서 stop 발사돼야 (-0.5% 초과).
    intents_static = mgr.evaluate(
        "NEARUSDT", Decimal("1.7400"), datetime.now(timezone.utc),
    )
    assert len(intents_static) == 1
    assert intents_static[0].side == "sell"

    # 다시 진입 (이전 stop 으로 cleanup 됨) + dynamic override 등록 (stop=0.02).
    store.record_fill(strategy_id="scan", symbol="NEARUSDT", side="buy", qty=Decimal("135"))
    pnl.record_fill(
        strategy_id="scan", symbol="NEARUSDT", side="buy",
        qty=Decimal("135"), price=Decimal("1.75"),
    )
    mgr.register_entry_override(
        "scan", "NEARUSDT", stop_loss_pct=0.02,  # -2% 까지 허용
    )
    # 1.7400 (-0.57%) 는 dynamic stop -2% 안이라 발사 안 돼야 함.
    intents_dyn = mgr.evaluate(
        "NEARUSDT", Decimal("1.7400"), datetime.now(timezone.utc),
    )
    assert intents_dyn == [], "dynamic stop=0.02 가 정적 0.005 를 덮어 더 멀리 허용"

    # 1.71 (-2.3%) 까지 가면 dynamic stop 도 발사.
    intents_dyn_fire = mgr.evaluate(
        "NEARUSDT", Decimal("1.71"), datetime.now(timezone.utc),
    )
    assert len(intents_dyn_fire) == 1


def test_override_persists_across_intent_to_fill_window():
    """Race fix (2026-05-21): BUY intent register_entry_override → broker fill
    도착 사이의 held=0 윈도우에서 evaluate() 가 한 번 이상 돌아도 override 는
    살아남아야 함. 이전 코드는 held=0 분기에서 _dynamic_policies 도 즉시 POP
    해버려서 fill 도착 후엔 정적 policy 로 fallback → NEAR 같은 변동성 큰 종목
    이 진입 직후 정적 trailing 0.5% 노이즈로 fire (실측 -0.28% churn).
    """
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    mgr = LivePositionRiskManager(position_store=store, pnl_aggregator=pnl)
    mgr.register_strategy_policy(
        "scan", stop_loss_pct=0.005, take_profit_pct=0.01, trailing_stop_pct=0.005,
    )

    # T1: BUY intent dispatch — register_entry_override 호출됨.
    mgr.register_entry_override(
        "scan", "NEARUSDT",
        stop_loss_pct=0.09, take_profit_pct=0.18, trailing_stop_pct=0.09,
    )
    assert ("scan", "NEARUSDT") in mgr._dynamic_policies

    # T2: broker fill 도착 전 — store/pnl 아직 비어있음 (held=0).
    # 이 윈도우에서 evaluate 가 한 번이라도 돌면 안 됨.
    for tick_price in ("1.75", "1.74", "1.76"):
        intents = mgr.evaluate(
            "NEARUSDT", Decimal(tick_price), datetime.now(timezone.utc),
        )
        assert intents == [], "no position → no intent"
    # KEY: override 가 살아남아야 함 (race fix 핵심).
    assert ("scan", "NEARUSDT") in mgr._dynamic_policies, (
        "_dynamic_policies must SURVIVE held=0 window between intent and fill"
    )

    # T3: broker fill 도착 — store/pnl 갱신, held > 0.
    store.record_fill(
        strategy_id="scan", symbol="NEARUSDT", side="buy", qty=Decimal("135"),
    )
    pnl.record_fill(
        strategy_id="scan", symbol="NEARUSDT", side="buy",
        qty=Decimal("135"), price=Decimal("1.76"),
    )

    # T4: mark price 가 진입가 대비 -0.3% — 정적 trailing 0.5% 면 high_water
    # 가 entry 위로만 살짝 갔어도 fire 가능. dynamic 9% trailing 이 살아있으면
    # 무시. 진입 직후 high_water 살짝 갱신을 모사 — entry 위 1.77 한 번 →
    # 1.7530 (-0.4% from peak) 으로 떨어짐.
    mgr.evaluate("NEARUSDT", Decimal("1.77"), datetime.now(timezone.utc))
    intents = mgr.evaluate("NEARUSDT", Decimal("1.7530"), datetime.now(timezone.utc))
    assert intents == [], (
        "dynamic 9% trailing/stop intact → 0.4% peak-pullback noise 무시되어야 함"
    )


def test_dynamic_policy_cleaned_up_after_stop_fires():
    """Stop 발사 → dynamic override 자동 cleanup → 다음 진입은 정적 fallback."""
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    mgr = LivePositionRiskManager(position_store=store, pnl_aggregator=pnl)
    mgr.register_strategy_policy(
        "scan", stop_loss_pct=0.005, take_profit_pct=0.01,
    )
    # 진입 + override.
    store.record_fill(strategy_id="scan", symbol="NEARUSDT", side="buy", qty=Decimal("135"))
    pnl.record_fill(
        strategy_id="scan", symbol="NEARUSDT", side="buy",
        qty=Decimal("135"), price=Decimal("1.75"),
    )
    mgr.register_entry_override("scan", "NEARUSDT", stop_loss_pct=0.02)
    assert ("scan", "NEARUSDT") in mgr._dynamic_policies

    # Stop 발사 (1.71 = -2.3%, dynamic stop -2% 초과).
    mgr.evaluate("NEARUSDT", Decimal("1.71"), datetime.now(timezone.utc))

    # 실제 청산은 broker fill 통해 store 에 반영되어야 dict cleanup. 시뮬:
    store.record_fill(strategy_id="scan", symbol="NEARUSDT", side="sell", qty=Decimal("135"))
    pnl.record_fill(
        strategy_id="scan", symbol="NEARUSDT", side="sell",
        qty=Decimal("135"), price=Decimal("1.71"),
    )
    # 다음 evaluate 에서 held=0 분기 진입 → dynamic 도 같이 cleanup.
    mgr.evaluate("NEARUSDT", Decimal("1.71"), datetime.now(timezone.utc))
    assert ("scan", "NEARUSDT") not in mgr._dynamic_policies


# ── Orchestrator _on_entry 콜백 forwarding ──────────────────────


class _ScannerWithOverride(LiveScannerMixin):
    stop_loss_pct: ClassVar[float] = 0.05  # 정적은 5% (느슨)
    take_profit_pct: ClassVar[float] = 0.10

    async def on_bar(self, ctx) -> Signal:
        return Signal(
            action="buy", size=0.05, reason="test",
            stop_loss_pct_override=0.02,
            take_profit_pct_override=0.04,
            trailing_stop_pct_override=0.015,
        )


def test_orchestrator_forwards_override_to_on_entry_callback():
    """Live-scanner BUY 통과 시 Signal override 가 _on_entry 로 전달."""
    orch = AsyncStrategyOrchestrator(Policy(policy_version=1, name="test"))
    orch.register_strategy("scan", _ScannerWithOverride())

    captured: list = []
    orch._on_entry = lambda sid, sym, **kw: captured.append((sid, sym, kw))

    snap = {
        "equity_krw": 1_000_000.0, "equity_usdt": 1_000_000.0,
        "ohlcv_history": {"BTCUSDT": _breakout_history()},
    }
    asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snap))
    assert len(captured) == 1
    sid, sym, kw = captured[0]
    assert sid == "scan"
    assert sym == "BTCUSDT"
    assert kw == {
        "stop_loss_pct": 0.02,
        "take_profit_pct": 0.04,
        "trailing_stop_pct": 0.015,
    }


def test_orchestrator_skips_callback_when_override_all_none():
    """Override 셋 모두 None 인 Signal (기존 backward compat) → 콜백 안 호출."""

    class _NoOverride(LiveScannerMixin):
        async def on_bar(self, ctx) -> Signal:
            return Signal(action="buy", size=0.05, reason="no_override")

    orch = AsyncStrategyOrchestrator(Policy(policy_version=1, name="test"))
    orch.register_strategy("scan", _NoOverride())
    captured: list = []
    orch._on_entry = lambda sid, sym, **kw: captured.append((sid, sym, kw))

    snap = {
        "equity_krw": 1_000_000.0, "equity_usdt": 1_000_000.0,
        "ohlcv_history": {"BTCUSDT": _breakout_history()},
    }
    asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snap))
    assert captured == []
