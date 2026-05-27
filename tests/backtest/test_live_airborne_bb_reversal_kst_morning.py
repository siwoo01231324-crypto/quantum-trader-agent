"""Unit tests for LiveAirborneBbReversalKstMorning (Pine v1.2 bidir + KST gate).

v1.2 signal correctness 는 [[signals.airborne_bb_reversal]] 의 helper-level
테스트가 박제하므로 본 모듈은:
  - 시간 게이트 ON/OFF 동작
  - bidir (buy = long, sell = short) signal emit
  - bb / atr warmup 시 hold
  - kst_entry_hours ctor override
  - tz-aware index 처리
만 검증한다.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_airborne_bb_reversal_kst_morning import (
    LiveAirborneBbReversalKstMorning,
    _bar_hour_kst,
)


def _ctx(history: pd.DataFrame, symbol: str = "BTCUSDT") -> dict:
    return {
        "ts": history.index[-1],
        "market_snapshot": {
            "symbol": symbol,
            "history": history,
            "price": float(history["close"].iloc[-1]),
        },
        "factors": {},
    }


def _run(strategy: LiveAirborneBbReversalKstMorning, ctx: dict) -> Signal | None:
    return asyncio.run(strategy.on_bar(ctx))


def _long_fire_frame_at_utc(last_utc: str) -> pd.DataFrame:
    """v1.2 long fire 가 -1 에서 터지는 합성 OHLC. last_utc 만 호출자 지정.

    구성:
      - 안정 상승 (base ~ 100) 으로 BB 안정화
      - 봉 -3: close 가 BB 하단 × (1-0.001) 미만으로 강하게 닫힘 + 큰 body →
        long setup arm
      - 봉 -2: extreme 갱신, close 아직 trigger 미만
      - 봉 -1: close 가 trigger 이상으로 회복 → BUY 발화
    """
    n = 50
    closes = np.linspace(100.0, 102.0, n).copy()
    opens = closes.copy()
    highs = closes + 0.5
    lows = closes - 0.5
    # 봉 -3: close=85, open=102 (큰 음봉) → BB 하단 한참 아래
    closes[-3], opens[-3], highs[-3], lows[-3] = 85.0, 102.0, 102.2, 84.0
    # 봉 -2: ext 갱신, close 81 < trigger
    closes[-2], opens[-2], highs[-2], lows[-2] = 81.0, 85.0, 85.5, 80.0
    # 봉 -1: close 95 >= trigger (=80+0.4*(85-80)=82)
    closes[-1], opens[-1], highs[-1], lows[-1] = 95.0, 81.0, 95.5, 80.0
    last = pd.Timestamp(last_utc)
    start = last - pd.Timedelta(hours=n - 1)
    idx = pd.date_range(start, periods=n, freq="1h")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": np.full(n, 1000.0)},
        index=idx,
    )


def _short_fire_frame_at_utc(last_utc: str) -> pd.DataFrame:
    """v1.2 short fire 가 -1 에서 터지는 합성 OHLC. _long_fire 의 mirror."""
    n = 50
    closes = np.linspace(100.0, 98.0, n).copy()
    opens = closes.copy()
    highs = closes + 0.5
    lows = closes - 0.5
    # 봉 -3: close=115, open=98 (큰 양봉) → BB 상단 한참 위
    closes[-3], opens[-3], highs[-3], lows[-3] = 115.0, 98.0, 116.0, 97.8
    # 봉 -2: ext 갱신, close 119 > trigger
    closes[-2], opens[-2], highs[-2], lows[-2] = 119.0, 115.0, 120.0, 114.5
    # 봉 -1: close 105 <= trigger (=120-0.4*(120-115)=118)
    closes[-1], opens[-1], highs[-1], lows[-1] = 105.0, 119.0, 120.0, 105.0
    last = pd.Timestamp(last_utc)
    start = last - pd.Timedelta(hours=n - 1)
    idx = pd.date_range(start, periods=n, freq="1h")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": np.full(n, 1000.0)},
        index=idx,
    )


class TestInheritance:
    def test_is_live_scanner(self):
        s = LiveAirborneBbReversalKstMorning()
        assert isinstance(s, LiveScannerMixin)
        assert s.is_live_scanner is True

    def test_stop_tp_defaults(self):
        s = LiveAirborneBbReversalKstMorning()
        assert s.stop_loss_pct == 0.03
        assert s.take_profit_pct == 0.06
        assert s.trailing_stop_pct is None

    def test_default_entry_hours_is_morning_block(self):
        s = LiveAirborneBbReversalKstMorning()
        assert s.kst_entry_hours == frozenset({6, 7, 8, 9, 10, 11})

    def test_default_pine_v12_params(self):
        s = LiveAirborneBbReversalKstMorning()
        assert s.min_close_margin == 0.001
        assert s.atr_period == 14
        assert s.atr_body_mult == 0.6


class TestTimeFilterLong:
    def test_buy_inside_morning_window(self):
        """KST 10:00 = UTC 01:00 → BUY emit."""
        s = LiveAirborneBbReversalKstMorning()
        history = _long_fire_frame_at_utc("2026-01-02T01:00:00")
        signal = _run(s, _ctx(history))
        assert signal.action == "buy", (
            f"expected buy, got {signal.action}/{signal.reason}"
        )
        assert "airborne_v12_long_fire" in signal.reason

    def test_hold_outside_morning_window(self):
        """KST 14:00 → 시간 게이트 차단."""
        s = LiveAirborneBbReversalKstMorning()
        history = _long_fire_frame_at_utc("2026-01-02T05:00:00")  # KST 14
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert signal.reason.startswith("time_filter:kst_hour=14")

    def test_boundary_06_pass(self):
        s = LiveAirborneBbReversalKstMorning()
        history = _long_fire_frame_at_utc("2026-01-01T21:00:00")  # KST 06
        signal = _run(s, _ctx(history))
        assert signal.action == "buy"

    def test_boundary_11_pass(self):
        s = LiveAirborneBbReversalKstMorning()
        history = _long_fire_frame_at_utc("2026-01-02T02:00:00")  # KST 11
        signal = _run(s, _ctx(history))
        assert signal.action == "buy"

    def test_boundary_12_blocked(self):
        s = LiveAirborneBbReversalKstMorning()
        history = _long_fire_frame_at_utc("2026-01-02T03:00:00")  # KST 12
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert "time_filter:kst_hour=12" in signal.reason


class TestTimeFilterShort:
    def test_sell_inside_morning_window(self):
        """SHORT 도 시간 통과 시 emit."""
        s = LiveAirborneBbReversalKstMorning()
        history = _short_fire_frame_at_utc("2026-01-02T01:00:00")  # KST 10
        signal = _run(s, _ctx(history))
        assert signal.action == "sell", (
            f"expected sell on short fire, got {signal.action}/{signal.reason}"
        )
        assert "airborne_v12_short_fire" in signal.reason

    def test_hold_outside_morning_window_for_short(self):
        """SHORT 도 시간 게이트 동일하게 차단."""
        s = LiveAirborneBbReversalKstMorning()
        history = _short_fire_frame_at_utc("2026-01-02T05:00:00")  # KST 14
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert signal.reason.startswith("time_filter:kst_hour=14")


class TestCtorKwargs:
    def test_custom_hours_override(self):
        s = LiveAirborneBbReversalKstMorning(kst_entry_hours=(20, 21, 22))
        assert s.kst_entry_hours == frozenset({20, 21, 22})
        history = _long_fire_frame_at_utc("2026-01-02T01:00:00")  # KST 10
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert "time_filter:kst_hour=10" in signal.reason

    def test_invalid_hour_raises(self):
        with pytest.raises(ValueError):
            LiveAirborneBbReversalKstMorning(kst_entry_hours=(24,))
        with pytest.raises(ValueError):
            LiveAirborneBbReversalKstMorning(kst_entry_hours=(-1, 6))

    def test_invalid_atr_period_raises(self):
        with pytest.raises(ValueError):
            LiveAirborneBbReversalKstMorning(atr_period=0)

    def test_invalid_margin_raises(self):
        with pytest.raises(ValueError):
            LiveAirborneBbReversalKstMorning(min_close_margin=-0.001)

    def test_invalid_body_mult_raises(self):
        with pytest.raises(ValueError):
            LiveAirborneBbReversalKstMorning(atr_body_mult=-0.1)


class TestWarmup:
    def test_hold_when_warmup(self):
        s = LiveAirborneBbReversalKstMorning()
        history = _long_fire_frame_at_utc("2026-01-02T01:00:00").iloc[:10]
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert "warmup" in signal.reason


class TestTzAwareIndex:
    def test_handles_tz_aware_utc(self):
        s = LiveAirborneBbReversalKstMorning()
        history = _long_fire_frame_at_utc("2026-01-02T01:00:00")
        history.index = history.index.tz_localize("UTC")
        signal = _run(s, _ctx(history))
        assert signal.action == "buy"

    def test_handles_tz_aware_kst(self):
        s = LiveAirborneBbReversalKstMorning()
        history = _long_fire_frame_at_utc("2026-01-02T01:00:00")
        history.index = history.index.tz_localize("UTC").tz_convert("Asia/Seoul")
        signal = _run(s, _ctx(history))
        assert signal.action == "buy"


class TestBarHourKstHelper:
    def test_naive_treated_as_utc(self):
        idx = pd.date_range("2026-01-01T15:00:00", periods=1, freq="1h")
        df = pd.DataFrame({"close": [100.0]}, index=idx)
        assert _bar_hour_kst(df) == 0  # UTC 15 → KST 0

    def test_empty_returns_none(self):
        assert _bar_hour_kst(pd.DataFrame({"close": []})) is None
