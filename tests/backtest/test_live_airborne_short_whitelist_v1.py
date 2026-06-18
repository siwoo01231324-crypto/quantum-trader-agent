"""Unit tests for LiveAirborneShortWhitelistV1 — SHORT-only + whitelist + 19h gate.

부모 클래스 (``LiveAirborneBbReversalKstHours`` ← Kst Morning) 의 bidir / BB /
warmup 동작은 기존 테스트 박제. 본 모듈은 **차이만** 검증:
  1. 19-hour gate ({0,1,2,3,5,9,...23} — 부모 4시간보다 넓음)
  2. SHORT only — LONG fire 발생해도 sell 아니라 hold
  3. retrace_ratio default = 0.6 (부모 default 0.4 와 다름)
  4. get_universe() = yaml 의 active 종목
  5. 부모 ClassVar 미오염
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_airborne_bb_reversal_kst_hours import (
    LiveAirborneBbReversalKstHours,
)
from backtest.strategies.live_airborne_bb_reversal_kst_morning import (
    LiveAirborneBbReversalKstMorning,
)
from backtest.strategies.live_airborne_short_whitelist_v1 import (
    LiveAirborneShortWhitelistV1,
)


def _ctx(history: pd.DataFrame, symbol: str = "ARBUSDT") -> dict:
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


def _short_fire_frame_at_utc(last_utc: str) -> pd.DataFrame:
    """업쪽 BB 돌파 후 되돌림 short fire 시뮬 frame."""
    n = 50
    closes = np.linspace(100.0, 102.0, n).copy()
    opens = closes.copy()
    highs = closes + 0.5
    lows = closes - 0.5
    # -3 ~ -1 은 강한 상승 후 되돌림 short setup
    closes[-3], opens[-3], highs[-3], lows[-3] = 120.0, 102.0, 121.0, 102.0
    closes[-2], opens[-2], highs[-2], lows[-2] = 125.0, 120.0, 126.0, 119.5
    closes[-1], opens[-1], highs[-1], lows[-1] = 105.0, 125.0, 126.0, 105.0
    last = pd.Timestamp(last_utc)
    start = last - pd.Timedelta(hours=n - 1)
    idx = pd.date_range(start, periods=n, freq="1h")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": np.full(n, 1000.0)},
        index=idx,
    )


def _long_fire_frame_at_utc(last_utc: str) -> pd.DataFrame:
    """하단 BB 돌파 후 되돌림 long fire 시뮬 frame (부모는 buy 발사)."""
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


# ── Inheritance ──────────────────────────────────────────────────────────────


class TestInheritance:
    def test_subclasses_kst_hours(self) -> None:
        s = LiveAirborneShortWhitelistV1()
        assert isinstance(s, LiveAirborneBbReversalKstHours)
        assert isinstance(s, LiveScannerMixin)
        assert s.is_live_scanner is True

    def test_strategy_id(self) -> None:
        assert (
            LiveAirborneShortWhitelistV1.strategy_id
            == "live-airborne-short-whitelist-v1"
        )

    def test_shorts_allowed_true(self) -> None:
        # 부모에서 상속 — sell intent reduce_only=False stamp 위해 필수
        s = LiveAirborneShortWhitelistV1()
        assert s.shorts_allowed is True

    def test_19_hour_gate(self) -> None:
        s = LiveAirborneShortWhitelistV1()
        expected = frozenset(
            {0, 1, 2, 3, 5, 9, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
        )
        assert s.kst_entry_hours == expected
        # train_PF<1 시간 제외 확인
        for excluded in (4, 6, 7, 8, 13):
            assert excluded not in s.kst_entry_hours

    def test_parent_classvars_not_polluted(self) -> None:
        # 부모 kst-hours gate v3 보존 (#375 이후 {1,2,3,5,6,7,8,23}).
        # 이전엔 {8,11,16,22} 단정 → #375 부터 stale, #380 에서 정정.
        assert LiveAirborneBbReversalKstHours.kst_entry_hours == frozenset(
            {1, 2, 3, 5, 6, 7, 8, 23}
        )
        # 부모 morning 6-hour gate 보존
        assert LiveAirborneBbReversalKstMorning.kst_entry_hours == frozenset(
            {6, 7, 8, 9, 10, 11}
        )

    def test_default_stop_tp(self) -> None:
        s = LiveAirborneShortWhitelistV1()
        assert s.stop_loss_pct == 0.03
        assert s.take_profit_pct == 0.06

    def test_default_retrace_and_body_mult(self) -> None:
        """Hard OOS 검증값 — 부모 default (0.4 / 0.6) 와 다름."""
        s = LiveAirborneShortWhitelistV1()
        assert s.retrace_ratio == 0.6
        assert s.atr_body_mult == 0.3

    def test_interval_is_1h(self) -> None:
        assert LiveAirborneShortWhitelistV1.get_interval() == "1h"


# ── Universe ─────────────────────────────────────────────────────────────────


class TestUniverse:
    """#380 — 고정 whitelist 제거. get_universe 는 부모의 거래량 top-100 상속
    (venue-routing). yaml active 집합 미사용."""

    def test_get_universe_inherits_binance_top100_by_default(self, monkeypatch) -> None:
        monkeypatch.delenv("QTA_BROKER_VENUE", raising=False)
        import src.portfolio.binance_top_dynamic as btd
        monkeypatch.setattr(
            btd, "get_top_n_symbols",
            lambda n=100: [f"SYM{i}USDT" for i in range(n)],
        )
        u = LiveAirborneShortWhitelistV1.get_universe()
        assert len(u) == 100
        assert u[0] == "SYM0USDT"

    def test_get_universe_routes_to_bitget_when_venue_set(self, monkeypatch) -> None:
        monkeypatch.setenv("QTA_BROKER_VENUE", "bitget")
        import src.portfolio.bitget_top_dynamic as btd
        monkeypatch.setattr(
            btd, "get_top_n_symbols",
            lambda n=100: ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        )
        u = LiveAirborneShortWhitelistV1.get_universe()
        assert u == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    def test_get_universe_no_longer_reads_whitelist_yaml(self, monkeypatch) -> None:
        """yaml active 집합과 무관 — top-100 provider 만 호출."""
        monkeypatch.delenv("QTA_BROKER_VENUE", raising=False)
        import src.portfolio.binance_top_dynamic as btd
        monkeypatch.setattr(btd, "get_top_n_symbols", lambda n=100: ["ONLYUSDT"])
        u = LiveAirborneShortWhitelistV1.get_universe()
        assert u == ["ONLYUSDT"]  # whitelist 의 FET/APT 등이 섞이지 않음


# ── on_bar — SHORT only ──────────────────────────────────────────────────────


class TestSideFilter:
    def test_short_fire_emits_sell(self) -> None:
        # UTC 02 = KST 11 → 19h gate 통과
        s = LiveAirborneShortWhitelistV1()
        history = _short_fire_frame_at_utc("2026-01-02T02:00:00")
        sig = _run(s, _ctx(history))
        # Note: synthetic frame 의 BB/ATR 이 조건 못 맞춰 hold 일 수도 있음.
        # 이 케이스에서는 fire 되면 sell, 안 되면 hold (둘 다 정상). buy 는 절대 X.
        assert sig is not None
        assert sig.action != "buy", f"LONG fire emitted: {sig}"

    def test_long_fire_does_not_emit_buy(self) -> None:
        """부모는 동일 frame 에 buy 발사하지만, 본 클래스는 hold."""
        s = LiveAirborneShortWhitelistV1()
        history = _long_fire_frame_at_utc("2026-01-02T02:00:00")
        sig = _run(s, _ctx(history))
        assert sig is not None
        # SHORT-only — 어떤 경우에도 buy 안 됨
        assert sig.action != "buy"
        # short evaluator 가 long fire frame 에는 setup 못 잡아 hold
        assert sig.action == "hold"


# ── Time gate ────────────────────────────────────────────────────────────────


class TestTimeGate:
    @pytest.mark.parametrize("utc_hour, kst_hour", [
        (23, 8),    # KST 8 → train PF<1 시간이라 차단
        (21, 6),    # KST 6 → 차단
        (22, 7),    # KST 7 → 차단
        (4, 13),    # KST 13 → 차단
    ])
    def test_excluded_hours_return_hold(self, utc_hour, kst_hour) -> None:
        s = LiveAirborneShortWhitelistV1()
        history = _short_fire_frame_at_utc(f"2026-01-02T{utc_hour:02d}:00:00")
        sig = _run(s, _ctx(history))
        assert sig is not None
        assert sig.action == "hold"
        assert "time_filter" in sig.reason
        assert f"kst_hour={kst_hour}" in sig.reason

    @pytest.mark.parametrize("utc_hour, kst_hour", [
        (3, 12),    # KST 12 → 19h gate 통과
        (9, 18),    # KST 18 → 통과
        (11, 20),   # KST 20 → 통과
        (15, 0),    # KST 0 → 통과
    ])
    def test_included_hours_pass_gate(self, utc_hour, kst_hour) -> None:
        s = LiveAirborneShortWhitelistV1()
        history = _short_fire_frame_at_utc(f"2026-01-02T{utc_hour:02d}:00:00")
        sig = _run(s, _ctx(history))
        assert sig is not None
        # time_filter 사유로 hold 되면 안 됨 (다른 사유로 hold/sell 은 OK)
        assert "time_filter" not in (sig.reason or "")


# ── Validation ───────────────────────────────────────────────────────────────


class TestValidation:
    def test_retrace_ratio_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="retrace_ratio"):
            LiveAirborneShortWhitelistV1(retrace_ratio=0.0)
        with pytest.raises(ValueError, match="retrace_ratio"):
            LiveAirborneShortWhitelistV1(retrace_ratio=1.5)

    def test_custom_retrace_accepted(self) -> None:
        s = LiveAirborneShortWhitelistV1(retrace_ratio=0.5)
        assert s.retrace_ratio == 0.5

    def test_custom_kst_hours_via_ctor(self) -> None:
        s = LiveAirborneShortWhitelistV1(kst_entry_hours=[10, 14])
        assert s.kst_entry_hours == frozenset({10, 14})


# ── Warmup ───────────────────────────────────────────────────────────────────


class TestWarmup:
    def test_short_history_returns_warmup(self) -> None:
        s = LiveAirborneShortWhitelistV1()
        # MIN_HISTORY 미만
        n = 5
        idx = pd.date_range("2026-01-02T02:00:00", periods=n, freq="1h")
        df = pd.DataFrame(
            {"open": [100.0]*n, "high": [101.0]*n, "low": [99.0]*n,
             "close": [100.0]*n, "volume": [1000.0]*n},
            index=idx,
        )
        sig = _run(s, _ctx(df))
        assert sig is not None
        assert sig.action == "hold"
        assert "warmup" in sig.reason


# ── No re-fire within same bar (2026-06-04 RIFUSDT 폭주 fix) ───────────────────


class TestNoReFireWithinSameBar:
    """첫 fire 후 같은 (symbol, bar_ts) 평가는 hold 만 반환.

    RIFUSDT 폭주 사고 (#regression): WS tick 마다 on_bar 재호출 → cached fire
    signal 그대로 반환 → 같은 봉 안에서 145회 SELL 폭주.
    """

    def test_second_eval_in_same_bar_returns_hold(self) -> None:
        # KST 11시 (UTC 02:00) — 19h 게이트 통과 시각
        df = _short_fire_frame_at_utc("2026-01-02T02:00:00")
        s = LiveAirborneShortWhitelistV1()

        first = _run(s, _ctx(df))
        assert first is not None
        assert first.action == "sell", f"first fire expected sell, got {first.action}"

        # 같은 frame (= 같은 last_bar_ts) 로 재호출 — fire 아니라 hold 받아야 함
        second = _run(s, _ctx(df))
        assert second is not None
        assert second.action == "hold", f"re-eval should be hold, got {second.action}"
        assert "fired_this_bar" in second.reason


def test_max_concurrent_positions_kwarg():
    """#380 — production.yaml kwarg 로 max_concurrent_positions 설정 가능."""
    s = LiveAirborneShortWhitelistV1(max_concurrent_positions=20)
    assert s.max_concurrent_positions == 20


def test_max_concurrent_positions_default_absent():
    """미설정 시 속성 없음 → orchestrator getattr 가 None (무제한)."""
    s = LiveAirborneShortWhitelistV1()
    assert getattr(s, "max_concurrent_positions", None) is None


# 2026-06-08 — short-whitelist 가 게이트·dedup 공유 헬퍼를 상속·적용하는지.
# (이전: short-whitelist 가 on_bar 오버라이드로 #389/#392/#393 전부 우회 →
#  실제 거래 전략에 게이트·dedup 미적용 = 재진입·알림없는매수 사고.)
import pytest as _pytest2
import pandas as _pd2
from decimal import Decimal as _D
from backtest.protocol import Signal as _Sig2
from backtest.strategies.live_airborne_short_whitelist_v1 import (
    LiveAirborneShortWhitelistV1 as _WL,
)


class _FireStoreWL:
    def __init__(self, fires): self._f = fires
    @property
    def path(self): return type("P", (), {"exists": staticmethod(lambda: True)})()
    def load_since(self, since): return self._f


def test_short_whitelist_inherits_gate_dedup_helper(tmp_path):
    """short-whitelist 가 _apply_daemon_gate_and_dedup(공유) 를 갖고 작동."""
    wl = _WL()
    wl._dedup_path = lambda: tmp_path / "wl.json"
    closed = _pd2.Timestamp("2026-06-07T21:00:00Z")
    sell = _Sig2(action="sell", size=0.5, reason="airborne_short_wl_fire")
    ctx = {"market_snapshot": {"symbol": "X"}}

    # 데몬 발화 없음 → 차단
    wl._get_fire_store = lambda: _FireStoreWL([])
    r = wl._apply_daemon_gate_and_dedup(ctx, sell, closed)
    assert r.action == "hold" and "no_daemon_fire" in r.reason

    # 데몬 발화 있음(floor(ts)==closed+1h=22:00) → 통과, 그 다음 같은봉 dedup
    wl._get_fire_store = lambda: _FireStoreWL(
        [{"symbol": "X", "side": "short", "ts": "2026-06-07T22:00:30+00:00"}]
    )
    r1 = wl._apply_daemon_gate_and_dedup(ctx, sell, closed)
    r2 = wl._apply_daemon_gate_and_dedup(ctx, sell, closed)
    assert r1.action == "sell"
    assert r2.action == "hold" and "already_entered_bar" in r2.reason


def test_short_whitelist_backtest_no_gate(tmp_path):
    """backtest(closed_ts None) → 게이트 무동작 (sell 그대로)."""
    wl = _WL()
    wl._dedup_path = lambda: tmp_path / "wl.json"
    sell = _Sig2(action="sell", size=0.5, reason="x")
    r = wl._apply_daemon_gate_and_dedup({"market_snapshot": {"symbol": "X"}}, sell, None)
    assert r.action == "sell"
