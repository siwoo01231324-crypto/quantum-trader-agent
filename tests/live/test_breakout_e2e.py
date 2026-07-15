"""돌파(live-donchian-breakout-btcgate) 엔드투엔드 체인 박제 — 2026-07-16.

돌파가 배포 이래 라이브 진입 0건이던 버그(유니버스 fetch 200 < MIN_HISTORY 205
→ 매 틱 warmup / mark-price 틱 심볼 4h 캐시가 얕은 1m 버퍼로 override) 수정 후,
**진입신호 → SL 등록 → 하드손절 발동 → 채널청산 발동** 전 과정이 실제 코드로
도는지 하나의 흐름으로 박제. 개별 조각(전략/ATR/SL/채널)은 각 단위 테스트가 있으나
체인은 여기서 처음 검증.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd

from backtest.strategies.live_donchian_breakout_btcgate import LiveDonchianBreakoutBtcGate
from src.live.pnl_aggregator import PnLAggregator
from src.live.strategy_position_store import StrategyPositionStore
from src.portfolio.live_position_risk import LivePositionRiskManager

_T0 = datetime(2026, 7, 16, 0, 0, 0, tzinfo=timezone.utc)
SID = "live-donchian-breakout-btcgate"


def _breakout_history(n: int = 210, base: float = 100.0, breakout: float = 105.0):
    """base 에서 평탄하다 마지막 봉에서 명확히 신고가 돌파하는 4h 시계열."""
    idx = pd.date_range("2026-05-01", periods=n, freq="4h")
    closes = [base] * (n - 1) + [breakout]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows, "close": closes,
         "volume": [1.0] * n}, index=idx,
    )


def _btc_uptrend(n: int = 210):
    """BTC 상승 → close > EMA200 (레짐 게이트 통과)."""
    idx = pd.date_range("2026-05-01", periods=n, freq="4h")
    closes = [90.0 + i * 0.05 for i in range(n)]
    return pd.DataFrame(
        {"open": closes, "high": [c + 1 for c in closes],
         "low": [c - 1 for c in closes], "close": closes,
         "volume": [1.0] * n}, index=idx,
    )


def _open_position(mgr_kwargs=None):
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    return store, pnl


def test_e2e_breakout_entry_then_hard_stop():
    """STEP 1~3: 돌파 진입신호(warmup 아님) → SL 등록 → 하드손절(2ATR) 발동."""
    strat = LiveDonchianBreakoutBtcGate(default_size=0.5, btc_regime_gate=True)
    hist = _breakout_history()
    btc = _btc_uptrend()
    ctx = {
        "live_run": True, "ts": _T0.isoformat(),
        "market_snapshot": {
            "symbol": "ETHUSDT", "history": hist,
            "universe_ohlcv": {"BTCUSDT": btc, "ETHUSDT": hist},
        },
    }

    # STEP 1 — 진입 신호. 210봉 ≥ MIN_HISTORY 205 라 warmup 아님(고쳤던 버그 지점).
    assert len(hist) >= strat.MIN_HISTORY
    sig = asyncio.run(strat.on_bar(ctx))
    assert sig is not None, "신호 None"
    assert sig.action == "buy", f"진입 신호가 buy 아님: {sig.action}:{sig.reason}"
    assert sig.stop_loss_pct_override and sig.stop_loss_pct_override > 0, "2ATR 손절 override 없음"

    # STEP 2 — 진입 체결 시뮬 + 리스크 등록(orch._on_entry 배선 재현).
    entry = float(hist["close"].iloc[-1])
    store, pnl = _open_position()
    pnl.record_fill(strategy_id=SID, symbol="ETHUSDT", side="buy",
                    qty=Decimal("1"), price=Decimal(str(entry)))
    store.record_fill(strategy_id=SID, symbol="ETHUSDT", side="buy", qty=Decimal("1"))
    mgr = LivePositionRiskManager(position_store=store, pnl_aggregator=pnl)
    mgr.register_strategy_policy(SID, stop_loss_pct=strat.stop_loss_pct,
                                take_profit_pct=strat.take_profit_pct)
    mgr.register_entry_override(SID, "ETHUSDT", stop_loss_pct=sig.stop_loss_pct_override)

    # STEP 3 — 가격이 entry−2ATR 아래로 → 하드손절 sell 발동.
    sl_price = entry * (1 - sig.stop_loss_pct_override) - 0.01
    intents = mgr.evaluate("ETHUSDT", Decimal(str(sl_price)), _T0)
    assert len(intents) == 1, f"손절 발동 안 됨: {intents}"
    assert intents[0].side == "sell"
    assert "stop_loss" in intents[0].reason


def test_e2e_channel_exit_fires_on_trend_break():
    """STEP 4: 보유 중 4h 종가가 Donchian10 하단 이탈 → 채널청산 sell 발동
    (돌파전략의 주청산 = TP 고정 아니라 채널추적)."""
    strat = LiveDonchianBreakoutBtcGate()
    entry = 105.0
    store, pnl = _open_position()
    pnl.record_fill(strategy_id=SID, symbol="ETHUSDT", side="buy",
                    qty=Decimal("1"), price=Decimal(str(entry)))
    store.record_fill(strategy_id=SID, symbol="ETHUSDT", side="buy", qty=Decimal("1"))
    mgr = LivePositionRiskManager(position_store=store, pnl_aggregator=pnl)
    mgr.register_strategy_policy(SID, stop_loss_pct=strat.stop_loss_pct,
                                take_profit_pct=strat.take_profit_pct)
    mgr.register_channel_exit(SID, strat.channel_exit_level)  # 실제 전략 채널 레벨 함수

    # 직전 10봉 low min = 100, 현재 종가 99 < 100 → 채널 이탈.
    idx = pd.date_range("2026-07-10", periods=12, freq="4h")
    lows = [100.0] * 11 + [90.0]      # 형성봉(마지막) 제외 직전10봉 low min = 100
    closes = [102.0] * 11 + [99.0]    # 현재 종가 99 < 채널 100
    exit_hist = pd.DataFrame(
        {"open": closes, "high": closes, "low": lows, "close": closes,
         "volume": [1.0] * 12}, index=idx,
    )
    intents = mgr.sweep_channel_exits(_T0, lambda sym: exit_hist)
    assert len(intents) == 1, f"채널청산 발동 안 됨: {intents}"
    assert intents[0].side == "sell"
    assert "channel_exit" in intents[0].reason


def test_e2e_channel_holds_when_close_above_channel():
    """추세 유지(종가 > 채널) → 청산 안 함(보유 유지). 조기청산 회귀 방지."""
    strat = LiveDonchianBreakoutBtcGate()
    store, pnl = _open_position()
    pnl.record_fill(strategy_id=SID, symbol="ETHUSDT", side="buy",
                    qty=Decimal("1"), price=Decimal("105"))
    store.record_fill(strategy_id=SID, symbol="ETHUSDT", side="buy", qty=Decimal("1"))
    mgr = LivePositionRiskManager(position_store=store, pnl_aggregator=pnl)
    mgr.register_strategy_policy(SID, stop_loss_pct=strat.stop_loss_pct,
                                take_profit_pct=strat.take_profit_pct)
    mgr.register_channel_exit(SID, strat.channel_exit_level)
    idx = pd.date_range("2026-07-10", periods=12, freq="4h")
    lows = [100.0] * 12
    closes = [110.0] * 12             # 종가 110 > 채널 100 → 유지
    hold_hist = pd.DataFrame(
        {"open": closes, "high": closes, "low": lows, "close": closes,
         "volume": [1.0] * 12}, index=idx,
    )
    assert mgr.sweep_channel_exits(_T0, lambda sym: hold_hist) == []
