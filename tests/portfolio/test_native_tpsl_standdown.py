"""P2 (2026-06-10) — synthetic SL/TP stand-down: 거래소 네이티브 preset TP/SL 이
활성인 종목은 LivePositionRiskManager 가 발동 안 함.

사고: synthetic 이 노이즈성 mark-price 틱에 거래소 TP/SL 라인 도달 전 조기청산
(CRDO +1.30% 에 live_stop_loss 오발동 등). native_tpsl_check 로 preset-active
종목을 skip → 거래소가 라인 청산 담당, synthetic 은 naked/청산분 백업.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from live.strategy_position_store import StrategyPositionStore
from src.live.pnl_aggregator import PnLAggregator
from src.portfolio.live_position_risk import LivePositionRiskManager


def _mgr_with_long(native_check=None) -> LivePositionRiskManager:
    """LONG +135 @1.75, stop 0.5% 등록한 risk manager. 1.7400(-0.57%)에서 stop 발사조건."""
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    mgr = LivePositionRiskManager(
        position_store=store, pnl_aggregator=pnl, native_tpsl_check=native_check,
    )
    mgr.register_strategy_policy("scan", stop_loss_pct=0.005, take_profit_pct=0.011)
    store.record_fill(
        strategy_id="scan", symbol="NEARUSDT", side="buy", qty=Decimal("135"),
    )
    pnl.record_fill(
        strategy_id="scan", symbol="NEARUSDT", side="buy",
        qty=Decimal("135"), price=Decimal("1.75"),
    )
    return mgr


def _eval(mgr):
    return mgr.evaluate("NEARUSDT", Decimal("1.7400"), datetime.now(timezone.utc))


def test_synthetic_fires_without_native_check():
    """native_tpsl_check 없음 → 기존대로 synthetic stop 발사 (회귀 가드)."""
    intents = _eval(_mgr_with_long(native_check=None))
    assert len(intents) == 1 and intents[0].side == "sell"


def test_synthetic_stands_down_when_native_active():
    """거래소 preset 활성(check True) → synthetic 발동 안 함 (핵심 P2)."""
    intents = _eval(_mgr_with_long(native_check=lambda sym: True))
    assert intents == [], "preset-active 종목은 거래소 TP/SL 담당 → synthetic skip"


def test_synthetic_fires_when_native_inactive():
    """preset 실패/naked(check False) → synthetic 백업 정상 발사."""
    intents = _eval(_mgr_with_long(native_check=lambda sym: False))
    assert len(intents) == 1


def test_set_native_tpsl_check_setter_path():
    """생성 후 setter 주입(loop wiring 경로)도 동작."""
    mgr = _mgr_with_long(native_check=None)
    mgr.set_native_tpsl_check(lambda sym: True)
    assert _eval(mgr) == []
    mgr.set_native_tpsl_check(None)  # 해제 시 다시 발사
    assert len(_eval(mgr)) == 1


def test_native_check_per_symbol():
    """check 가 종목별로 동작 — 다른 종목엔 영향 없음."""
    mgr = _mgr_with_long(native_check=lambda sym: sym == "OTHERUSDT")
    assert len(_eval(mgr)) == 1  # NEARUSDT 는 native 아님 → 발사
