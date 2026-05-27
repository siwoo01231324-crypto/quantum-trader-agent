"""Unit tests for LiveAirborneBbReversalKstHours (KST 8/11/16/22 hours gate).

대부분의 v1.2 bidir 동작은 [[test_live_airborne_bb_reversal_kst_morning]] 가
이미 박제. 본 모듈은 *시각 게이트 차이* 만 검증:
  - kst_entry_hours = {8, 11, 16, 22}
  - {8, 11, 16, 22} 만 진입 통과, 다른 시각 차단
  - 7시 (KST_MORNING 에서는 통과) → 차단 확인 (set 이 다름)
  - 22시 (KST_MORNING 에서는 차단) → 통과 확인
  - 부모 클래스 ClassVar 미오염 (instance shadow 작동)
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
)
from backtest.strategies.live_airborne_bb_reversal_kst_hours import (
    LiveAirborneBbReversalKstHours,
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


def _run(strategy, ctx: dict) -> Signal | None:
    return asyncio.run(strategy.on_bar(ctx))


def _long_fire_frame_at_utc(last_utc: str) -> pd.DataFrame:
    n = 50
    closes = np.linspace(100.0, 102.0, n).copy()
    opens = closes.copy()
    highs = closes + 0.5
    lows = closes - 0.5
    closes[-3], opens[-3], highs[-3], lows[-3] = 85.0, 102.0, 102.2, 84.0
    closes[-2], opens[-2], highs[-2], lows[-2] = 81.0, 85.0, 85.5, 80.0
    closes[-1], opens[-1], highs[-1], lows[-1] = 95.0, 81.0, 95.5, 80.0
    last = pd.Timestamp(last_utc)
    start = last - pd.Timedelta(hours=n - 1)
    idx = pd.date_range(start, periods=n, freq="1h")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": np.full(n, 1000.0)},
        index=idx,
    )


class TestInheritance:
    def test_subclasses_morning(self):
        s = LiveAirborneBbReversalKstHours()
        assert isinstance(s, LiveAirborneBbReversalKstMorning)
        assert isinstance(s, LiveScannerMixin)
        assert s.is_live_scanner is True

    def test_kst_entry_hours_is_top4(self):
        s = LiveAirborneBbReversalKstHours()
        assert s.kst_entry_hours == frozenset({8, 11, 16, 22})

    def test_parent_classvar_not_polluted(self):
        """Subclass override 가 parent ClassVar 를 변경하면 안 됨."""
        # Parent 의 default (morning block) 보존
        assert LiveAirborneBbReversalKstMorning.kst_entry_hours == frozenset({6, 7, 8, 9, 10, 11})
        # Subclass 만 새 set
        assert LiveAirborneBbReversalKstHours.kst_entry_hours == frozenset({8, 11, 16, 22})

    def test_stop_tp_inherited(self):
        s = LiveAirborneBbReversalKstHours()
        assert s.stop_loss_pct == 0.03
        assert s.take_profit_pct == 0.06


class TestTimeGate:
    """5y bench 의 4 시각만 통과."""

    @pytest.mark.parametrize("utc_hour, kst_hour", [
        (23, 8),    # UTC 23 = KST 8 → PASS
        (2,  11),   # UTC 02 = KST 11 → PASS
        (7,  16),   # UTC 07 = KST 16 → PASS
        (13, 22),   # UTC 13 = KST 22 → PASS
    ])
    def test_passes_top_hours(self, utc_hour, kst_hour):
        s = LiveAirborneBbReversalKstHours()
        history = _long_fire_frame_at_utc(f"2026-01-02T{utc_hour:02d}:00:00")
        signal = _run(s, _ctx(history))
        assert signal.action == "buy", (
            f"KST {kst_hour}시 (UTC {utc_hour}) 진입 기대, got {signal.action}/{signal.reason}"
        )

    @pytest.mark.parametrize("utc_hour, kst_hour", [
        (21, 6),    # KST 6 — morning 에선 통과, hours 에선 차단
        (22, 7),    # KST 7 — morning 통과, hours 차단
        (0,  9),    # KST 9 — morning 통과, hours 차단
        (1,  10),   # KST 10 — morning 통과, hours 차단
        (3,  12),   # KST 12 — 둘 다 차단
        (5,  14),   # KST 14 — 둘 다 차단
        (15, 0),    # KST 0 — 둘 다 차단
    ])
    def test_blocks_other_hours(self, utc_hour, kst_hour):
        s = LiveAirborneBbReversalKstHours()
        history = _long_fire_frame_at_utc(f"2026-01-02T{utc_hour:02d}:00:00")
        signal = _run(s, _ctx(history))
        assert signal.action == "hold", (
            f"KST {kst_hour}시 차단 기대, got {signal.action}/{signal.reason}"
        )
        assert signal.reason.startswith("time_filter:")
        assert f"kst_hour={kst_hour}_" in signal.reason


class TestDifferenceFromMorning:
    """KST 7시 — morning 통과, hours 차단 (정확한 분기 검증)."""

    def test_kst7_morning_passes(self):
        s = LiveAirborneBbReversalKstMorning()
        history = _long_fire_frame_at_utc("2026-01-02T22:00:00")  # KST 7
        signal = _run(s, _ctx(history))
        assert signal.action == "buy"

    def test_kst7_hours_blocks(self):
        s = LiveAirborneBbReversalKstHours()
        history = _long_fire_frame_at_utc("2026-01-02T22:00:00")  # KST 7
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert "time_filter:kst_hour=7" in signal.reason

    def test_kst22_morning_blocks(self):
        s = LiveAirborneBbReversalKstMorning()
        history = _long_fire_frame_at_utc("2026-01-02T13:00:00")  # KST 22
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"

    def test_kst22_hours_passes(self):
        s = LiveAirborneBbReversalKstHours()
        history = _long_fire_frame_at_utc("2026-01-02T13:00:00")  # KST 22
        signal = _run(s, _ctx(history))
        assert signal.action == "buy"


class TestCtorOverride:
    """공통 부모의 ctor kwarg 가 작동."""
    def test_custom_hours_via_ctor(self):
        s = LiveAirborneBbReversalKstHours(kst_entry_hours=(20, 21))
        assert s.kst_entry_hours == frozenset({20, 21})
        history = _long_fire_frame_at_utc("2026-01-02T02:00:00")  # KST 11
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"  # 11 ∉ {20, 21}
