"""Unit tests for LiveAirborneBbReversalKstHours (KST {1,2,3,6,7,8,23} hours gate, v3).

대부분의 v1.2 bidir 동작은 [[test_live_airborne_bb_reversal_kst_morning]] 가
이미 박제. 본 모듈은 *시각 게이트 차이* 만 검증:
  - kst_entry_hours = {1, 2, 3, 6, 7, 8, 23} (v3, 13일 1m 기반)
  - {1,2,3,6,7,8,23} 만 진입 통과, 다른 시각 차단
  - 22시 (v2 에서는 통과) → v3 에서 차단 확인 (set 이 다름)
  - 1시 (KST_MORNING 에서는 차단) → v3 에서 통과 확인
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

    def test_kst_entry_hours_is_top5(self):
        """v3 set (2026-06-06) — 13일 1m 실측 기반 새벽~아침+23시 {1,2,3,6,7,8,23}."""
        s = LiveAirborneBbReversalKstHours()
        assert s.kst_entry_hours == frozenset({1, 2, 3, 6, 7, 8, 23})

    def test_parent_classvar_not_polluted(self):
        """Subclass override 가 parent ClassVar 를 변경하면 안 됨."""
        # Parent 의 default (morning block) 보존
        assert LiveAirborneBbReversalKstMorning.kst_entry_hours == frozenset({6, 7, 8, 9, 10, 11})
        # Subclass 만 새 set (v3 — 새벽~아침+23시 {1,2,3,6,7,8,23})
        assert LiveAirborneBbReversalKstHours.kst_entry_hours == frozenset({1, 2, 3, 6, 7, 8, 23})

    def test_stop_tp_inherited(self):
        s = LiveAirborneBbReversalKstHours()
        assert s.stop_loss_pct == 0.03
        assert s.take_profit_pct == 0.06


class TestTimeGate:
    """v3 set (2026-06-06) — {1,2,3,6,7,8,23}만 통과. 13일 1m 실측 기반."""

    @pytest.mark.parametrize("utc_hour, kst_hour", [
        (16, 1),    # UTC 16 = KST 1 → PASS (v3 새벽)
        (17, 2),    # UTC 17 = KST 2 → PASS (v3 새벽)
        (18, 3),    # UTC 18 = KST 3 → PASS (v3 새벽)
        (21, 6),    # UTC 21 = KST 6 → PASS (v3 아침)
        (22, 7),    # UTC 22 = KST 7 → PASS (v3 아침)
        (23, 8),    # UTC 23 = KST 8 → PASS (v3 아침)
        (14, 23),   # UTC 14 = KST 23 → PASS (v3 신규, 23시 숏 PF 2.09)
    ])
    def test_passes_top_hours(self, utc_hour, kst_hour):
        s = LiveAirborneBbReversalKstHours()
        history = _long_fire_frame_at_utc(f"2026-01-02T{utc_hour:02d}:00:00")
        signal = _run(s, _ctx(history))
        assert signal.action == "buy", (
            f"KST {kst_hour}시 (UTC {utc_hour}) 진입 기대, got {signal.action}/{signal.reason}"
        )

    @pytest.mark.parametrize("utc_hour, kst_hour", [
        (7,  16),   # KST 16 — v2 에선 통과, v3 에서 차단
        (11, 20),   # KST 20 — v2 에선 통과, v3 에서 차단
        (13, 22),   # KST 22 — v2 에선 통과, v3 에서 차단
        (2,  11),   # KST 11 — 둘 다 차단
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
    """v3 set 의 morning 과의 차이 — KST 7/8 양쪽 통과 + KST 22 hours 만 차단."""

    def test_kst7_morning_passes(self):
        s = LiveAirborneBbReversalKstMorning()
        history = _long_fire_frame_at_utc("2026-01-02T22:00:00")  # KST 7
        signal = _run(s, _ctx(history))
        assert signal.action == "buy"

    def test_kst7_hours_passes_v3(self):
        """v3 (2026-06-06) — KST 7 포함. UTC 22 = KST 7."""
        s = LiveAirborneBbReversalKstHours()
        history = _long_fire_frame_at_utc("2026-01-02T22:00:00")  # KST 7
        signal = _run(s, _ctx(history))
        assert signal.action == "buy"

    def test_kst11_morning_passes(self):
        s = LiveAirborneBbReversalKstMorning()
        history = _long_fire_frame_at_utc("2026-01-02T02:00:00")  # KST 11
        signal = _run(s, _ctx(history))
        assert signal.action == "buy"

    def test_kst11_hours_blocks_v3(self):
        """v3 (2026-06-06) — KST 11 제외 (v2 에서도 제외였음)."""
        s = LiveAirborneBbReversalKstHours()
        history = _long_fire_frame_at_utc("2026-01-02T02:00:00")  # KST 11
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert "time_filter:kst_hour=11" in signal.reason

    def test_kst22_morning_blocks(self):
        s = LiveAirborneBbReversalKstMorning()
        history = _long_fire_frame_at_utc("2026-01-02T13:00:00")  # KST 22
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"

    def test_kst22_hours_blocks_v3(self):
        """v3 (2026-06-06) — KST 22 제외. v2 에선 통과였으나 v3 에서 차단."""
        s = LiveAirborneBbReversalKstHours()
        history = _long_fire_frame_at_utc("2026-01-02T13:00:00")  # KST 22
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert "time_filter:kst_hour=22" in signal.reason


class TestCtorOverride:
    """공통 부모의 ctor kwarg 가 작동."""
    def test_custom_hours_via_ctor(self):
        s = LiveAirborneBbReversalKstHours(kst_entry_hours=(20, 21))
        assert s.kst_entry_hours == frozenset({20, 21})
        history = _long_fire_frame_at_utc("2026-01-02T02:00:00")  # KST 11
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"  # 11 ∉ {20, 21}


# ─────────────────────────────────────────────────────────────────────────────
# 2026-06-08 봉마감 게이트 회귀 테스트 (PIPPINUSDT 미완성봉 발화 사고)
# ─────────────────────────────────────────────────────────────────────────────
import pandas as _pd
import pytest as _pytest
from unittest import mock as _mock
from backtest.protocol import Signal as _Signal
from backtest.strategies.live_airborne_bb_reversal_kst_hours import (
    LiveAirborneBbReversalKstHours as _S,
)
from backtest.strategies.live_airborne_bb_reversal_kst_morning import (
    LiveAirborneBbReversalKstMorning as _Parent,
)

_IDX = _pd.date_range("2026-06-07T17:00:00Z", periods=6, freq="1h")
_HIST = _pd.DataFrame(
    {"open": 1.0, "high": 1.0, "low": 1.0, "close": [1, 2, 3, 4, 5, 6]}, index=_IDX
)


def test_gate_backtest_no_live_run_is_unchanged():
    st = _S()
    ctx = {"ts": _IDX[-1], "market_snapshot": {"symbol": "X", "history": _HIST}}
    gated, closed_ts = st._bar_close_gate(ctx)
    assert gated is ctx and closed_ts is None  # byte-identical backtest path
    assert len(gated["market_snapshot"]["history"]) == 6


def test_gate_live_forming_bar_trims_to_closed():
    st = _S()
    ctx = {
        "ts": _pd.Timestamp("2026-06-07T22:27:00Z"),  # 22:00봉 형성 중
        "live_run": True,
        "market_snapshot": {"symbol": "X", "history": _HIST},
    }
    gated, closed_ts = st._bar_close_gate(ctx)
    assert len(gated["market_snapshot"]["history"]) == 5  # 미완성봉 제거
    assert closed_ts == _pd.Timestamp("2026-06-07T21:00:00Z")  # 마감봉


def test_gate_live_closed_bar_no_trim():
    st = _S()
    ctx = {
        "ts": _pd.Timestamp("2026-06-07T23:05:00Z"),  # 22:00봉 이미 마감
        "live_run": True,
        "market_snapshot": {"symbol": "X", "history": _HIST},
    }
    gated, closed_ts = st._bar_close_gate(ctx)
    assert len(gated["market_snapshot"]["history"]) == 6
    assert closed_ts == _pd.Timestamp("2026-06-07T22:00:00Z")


async def _fake_buy(self, ctx):
    return _Signal(action="buy", size=0.5, reason="airborne_long_fire")


def _isolated(st, tmp_path):
    """dedup 영속 파일을 tmp 로 격리 (테스트 간 오염 방지)."""
    p = tmp_path / "dedup.json"
    st._dedup_path = lambda: p
    return p


@_pytest.mark.asyncio
async def test_on_bar_dedup_one_entry_per_closed_bar(tmp_path):
    """같은 마감봉엔 한 번만 진입 (TP/SL 청산 후 재진입 폭주 방지)."""
    st = _S(btc_trend_filter_enabled=False)
    _isolated(st, tmp_path)
    ctx = {
        "ts": _pd.Timestamp("2026-06-07T22:27:00Z"),
        "live_run": True,
        "market_snapshot": {"symbol": "X", "history": _HIST},
    }
    with _mock.patch.object(_Parent, "on_bar", _fake_buy):
        sig1 = await st.on_bar(ctx)
        sig2 = await st.on_bar(ctx)  # 같은 마감봉 재평가
    assert sig1.action == "buy"
    assert sig2.action == "hold"
    assert "reentry_cooldown" in sig2.reason


@_pytest.mark.asyncio
async def test_reentry_cooldown_blocks_next_bar(tmp_path):
    """쿨다운(12h) 안의 *다음* 봉도 재진입 차단 (매시간 재매수 방지)."""
    st = _S(btc_trend_filter_enabled=False)
    _isolated(st, tmp_path)
    # 22:00봉 진입 → 23:00봉(1h 뒤, 쿨다운 12h 안) 차단
    ctx1 = {"ts": _pd.Timestamp("2026-06-07T22:27:00Z"), "live_run": True,
            "market_snapshot": {"symbol": "X", "history": _HIST}}
    hist2 = _pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": [1, 2, 3, 4, 5, 6, 7]},
        index=_pd.date_range("2026-06-07T17:00:00Z", periods=7, freq="1h"),
    )
    ctx2 = {"ts": _pd.Timestamp("2026-06-07T23:27:00Z"), "live_run": True,
            "market_snapshot": {"symbol": "X", "history": hist2}}
    with _mock.patch.object(_Parent, "on_bar", _fake_buy):
        sig1 = await st.on_bar(ctx1)   # 22:00봉(closed 21:00) 진입
        sig2 = await st.on_bar(ctx2)   # 23:00봉(closed 22:00) — 1h 뒤
    assert sig1.action == "buy"
    assert sig2.action == "hold" and "reentry_cooldown" in sig2.reason


@_pytest.mark.asyncio
async def test_reentry_persists_across_restart(tmp_path):
    """재시작(새 인스턴스)해도 dedup 디스크에서 복원 → 재매수 안 함."""
    p = tmp_path / "dedup.json"
    ctx = {"ts": _pd.Timestamp("2026-06-07T22:27:00Z"), "live_run": True,
           "market_snapshot": {"symbol": "X", "history": _HIST}}
    with _mock.patch.object(_Parent, "on_bar", _fake_buy):
        st1 = _S(btc_trend_filter_enabled=False); st1._dedup_path = lambda: p
        sig1 = await st1.on_bar(ctx)
        # 새 인스턴스(재시작 시뮬) — 같은 dedup 파일 로드
        st2 = _S(btc_trend_filter_enabled=False); st2._dedup_path = lambda: p
        sig2 = await st2.on_bar(ctx)
    assert sig1.action == "buy"
    assert sig2.action == "hold"  # 재시작해도 차단됨
    assert p.exists()


@_pytest.mark.asyncio
async def test_backtest_no_live_run_unaffected_by_dedup(tmp_path):
    """backtest(live_run 없음)는 dedup 영속 무관 — 매 봉 평가 (byte-identical)."""
    st = _S(btc_trend_filter_enabled=False)
    _isolated(st, tmp_path)
    ctx = {"ts": _HIST.index[-1],  # backtest 컨벤션, live_run 없음
           "market_snapshot": {"symbol": "X", "history": _HIST}}
    with _mock.patch.object(_Parent, "on_bar", _fake_buy):
        sig1 = await st.on_bar(ctx)
        sig2 = await st.on_bar(ctx)
    assert sig1.action == "buy" and sig2.action == "buy"  # dedup 안 걸림
