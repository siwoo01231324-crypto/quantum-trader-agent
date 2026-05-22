"""2026-05-22 — 레버리지 트레이딩용 ROI 기반 익절/손절 회귀.

레버리지 트레이딩에서 익절/손절 직관은 가격 변동이 아니라 ROI(증거금
수익률). `LiveScannerMixin._apply_roi_targets` 가 take_profit_roi /
stop_loss_roi 를 가격 pct = ROI / leverage 로 환산해 take_profit_pct /
stop_loss_pct 를 덮어쓴다. 4개 live-scanner 전략 공통.
"""
from __future__ import annotations

import pytest

from backtest.strategies.live_bb_lower_bounce import LiveBbLowerBounce
from backtest.strategies.live_breakout_with_atr_stop import LiveBreakoutWithAtrStop
from backtest.strategies.live_oversold_with_divergence import LiveOversoldWithDivergence
from backtest.strategies.live_rsi_oversold_volume_spike import LiveRsiOversoldVolumeSpike

_ALL = [
    LiveBreakoutWithAtrStop,
    LiveRsiOversoldVolumeSpike,
    LiveBbLowerBounce,
    LiveOversoldWithDivergence,
]


@pytest.mark.parametrize("cls", _ALL)
def test_roi_targets_convert_to_price_pct(cls):
    """★ ROI 12%/8% @ leverage 10 → 가격 pct 1.2%/0.8%.

    LivePositionRiskManager 는 가격 기준 pct (entry×(1±pct)) 로 청산
    평가하므로, ROI 입력을 leverage 로 나눠 가격 pct 로 환산해야 한다.
    """
    s = cls(
        default_size=0.3,
        take_profit_roi=0.12, stop_loss_roi=0.08, leverage=10,
    )
    assert s.take_profit_pct == pytest.approx(0.012)
    assert s.stop_loss_pct == pytest.approx(0.008)


@pytest.mark.parametrize("cls", _ALL)
def test_roi_none_preserves_static_pct(cls):
    """ROI 인자 미지정 → 정적 pct 동작 보존 (기존 회귀 무영향)."""
    s = cls(default_size=0.3, stop_loss_pct=0.005, take_profit_pct=0.01)
    assert s.take_profit_pct == pytest.approx(0.01)
    assert s.stop_loss_pct == pytest.approx(0.005)


def test_roi_leverage_scaling():
    """leverage 가 바뀌면 같은 ROI 목표가 다른 가격 pct — leverage-aware."""
    s5 = LiveBreakoutWithAtrStop(
        default_size=0.3, take_profit_roi=0.12, leverage=5,
    )
    s20 = LiveBreakoutWithAtrStop(
        default_size=0.3, take_profit_roi=0.12, leverage=20,
    )
    assert s5.take_profit_pct == pytest.approx(0.024)   # 12% / 5x
    assert s20.take_profit_pct == pytest.approx(0.006)  # 12% / 20x


def test_roi_requires_positive_leverage():
    """ROI 를 주고 leverage 누락/0/음수 → ValueError."""
    with pytest.raises(ValueError, match="leverage"):
        LiveBreakoutWithAtrStop(default_size=0.3, take_profit_roi=0.12)
    with pytest.raises(ValueError, match="leverage"):
        LiveBreakoutWithAtrStop(
            default_size=0.3, take_profit_roi=0.12, leverage=0,
        )


def test_roi_rejects_nonpositive_target():
    """ROI 목표가 0 이하 → ValueError."""
    with pytest.raises(ValueError, match="take_profit_roi"):
        LiveBreakoutWithAtrStop(
            default_size=0.3, take_profit_roi=0.0, leverage=10,
        )
    with pytest.raises(ValueError, match="stop_loss_roi"):
        LiveBreakoutWithAtrStop(
            default_size=0.3, stop_loss_roi=-0.1, leverage=10,
        )


def test_roi_partial_only_tp():
    """take_profit_roi 만 줘도 동작 — stop 은 정적 유지."""
    s = LiveBreakoutWithAtrStop(
        default_size=0.3, take_profit_roi=0.12, leverage=10,
        stop_loss_pct=0.005,
    )
    assert s.take_profit_pct == pytest.approx(0.012)
    assert s.stop_loss_pct == pytest.approx(0.005)  # 정적 유지
