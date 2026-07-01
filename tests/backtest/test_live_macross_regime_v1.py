"""Unit tests for LiveMacrossRegime (1h SMA25/200 cross + BTC SMA200 regime gate).

strategy id: live-macross-regime-v1 (id-snake: live_macross_regime_v1).
module: backtest.strategies.live_macross_regime_v1.LiveMacrossRegime.

on_bar 단위 검증 (orchestrator dispatch 불필요):
  (a) 골든크로스 + BTC 상승장        → buy
  (b) 데드크로스 + BTC 하락장        → sell
  (c) 골든크로스 + BTC 하락장 (역행) → hold
  (d) 데드크로스 + BTC 상승장 (역행) → hold
  (e) warmup (봉 < 202)             → hold
  (f) BTC 데이터 없음               → hold (보수적 skip)
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_macross_regime_v1 import (
    LiveMacrossRegime,
    _slow_slope,
    detect_cross,
)

_FAST = 25
_SLOW = 200
_N = _SLOW + 5  # 205 — MIN_HISTORY(202) 통과


def _run(strategy, ctx: dict) -> Signal | None:
    return asyncio.run(strategy.on_bar(ctx))


def _frame_from_closes(closes: np.ndarray) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + 0.5,
            "low": closes - 0.5,
            "close": closes,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def _golden_cross_frame() -> pd.DataFrame:
    """마지막 확정봉에서 fast SMA 가 slow SMA 를 상향 돌파하도록 구성.

    평평한 100 baseline → 마지막 1봉만 단발 급등. 이러면 bar -2 에서는
    fast==slow(100), bar -1 에서 fast > slow → 정확히 마지막 봉에서 골든크로스.
    단, SMA200 은 거의 flat → slope 필터를 통과시키려면
    _golden_cross_trending_frame() 을 사용한다.
    """
    closes = np.full(_N, 100.0)
    closes[-1] = 100.0 + 1.0 * _FAST  # fast 평균을 +1 끌어올려 slow 추월
    return _frame_from_closes(closes)


def _death_cross_frame() -> pd.DataFrame:
    """마지막 확정봉에서 fast SMA 가 slow SMA 를 하향 돌파하도록 구성 (mirror).

    SMA200 이 거의 flat → slope 필터 통과는 _death_cross_trending_frame() 사용.
    """
    closes = np.full(_N, 100.0)
    closes[-1] = 100.0 - 1.0 * _FAST  # fast 평균을 -1 끌어내려 slow 하향 돌파
    return _frame_from_closes(closes)


def _make_trending_cross_frame(direction: str) -> pd.DataFrame:
    """골든/데드크로스 + SMA200 기울기 + ADX≥20 을 모두 만족하는 범용 프레임.

    수학적 보장:
    ┌ 골든크로스(direction="up"):
    │  close[0..203] = 90  (전부 90, SMA25==SMA200==90)
    │  close[204]    = 200 (급등)
    │  → SMA25[-2]=90, SMA200[-2]=90  (fast<=slow ✓)
    │  → SMA25[-1]=(24×90+200)/25=94.4, SMA200[-1]=(199×90+200)/200=90.55
    │  → fast(94.4) > slow(90.55) ✓ 골든크로스
    │  SMA200 slope: SMA200[-1]=90.55 > SMA200[-6]=(194×90+200-5×90+5×90)/200
    │    = SMA200[-6] = mean(close[5..204-5]) = mean(close[5..199])=90
    │    90.55 > 90 → slope="up" ✓
    │
    └ 데드크로스(direction="down"):
      close[0..203] = 110 (전부 110)
      close[204]    = 0   (급락)
      → SMA25[-2]=110, SMA200[-2]=110 (fast>=slow ✓)
      → SMA25[-1]=(24×110+0)/25=105.6, SMA200[-1]=(199×110+0)/200=109.45
      → fast(105.6) < slow(109.45) ✓ 데드크로스
      SMA200 slope: SMA200[-1]=109.45 < SMA200[-6]=110 → slope="down" ✓

    ADX≥20 보장:
      각 봉 high/low 비대칭으로 +DM(골든) 또는 -DM(데드) 누적:
        골든: high=close+10, low=close-1  → up_move=9, down_move 없음 → +DM 누적
        데드: high=close+1,  low=close-10 → down_move=9, up_move 없음  → -DM 누적
      TR=11 일정, +DM/TR 또는 -DM/TR 이 크면 DI 차이 큼 → ADX 상승.
      마지막 봉(급등/급락)은 TR 크지만 전체에 비해 1봉이라 ADX 영향 최소.
    """
    n = _N
    base = 90.0 if direction == "up" else 110.0
    spike = 200.0 if direction == "up" else 0.0
    closes = np.full(n, base)
    closes[-1] = spike

    if direction == "up":
        # +DM 우위: 매봉 high 가 prev_high 보다 크게, low 는 거의 안 내려감
        # 방법: close 가 flat 이지만 high 를 매봉 +0.5씩 올림
        highs = np.array([base + i * 0.05 + 10.0 for i in range(n)])
        lows  = np.full(n, base - 1.0)
    else:
        # -DM 우위: low 가 매봉 -0.5씩 내려감
        highs = np.full(n, base + 1.0)
        lows  = np.array([base - i * 0.05 - 10.0 for i in range(n)])

    # 마지막 봉 high/low 는 spike 기준
    highs[-1] = spike + 5.0
    lows[-1]  = max(spike - 5.0, 0.01)

    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows,
         "close": closes, "volume": np.full(n, 1000.0)},
        index=idx,
    )


def _golden_cross_trending_frame() -> pd.DataFrame:
    """골든크로스 + SMA200 상향 기울기 + ADX≥20."""
    return _make_trending_cross_frame("up")


def _death_cross_trending_frame() -> pd.DataFrame:
    """데드크로스 + SMA200 하향 기울기 + ADX≥20."""
    return _make_trending_cross_frame("down")


def _btc_frame(regime: str) -> pd.DataFrame:
    """BTC 레짐 프레임. 'up' → close ≥ SMA200, 'down' → close < SMA200."""
    n = _SLOW + 5
    if regime == "up":
        # 꾸준한 상승 → 마지막 close 가 SMA200(과거 평균) 위.
        closes = np.linspace(20000.0, 40000.0, n)
    else:
        # 꾸준한 하락 → 마지막 close 가 SMA200 아래.
        closes = np.linspace(40000.0, 20000.0, n)
    return _frame_from_closes(closes)


def _ctx(history: pd.DataFrame, btc_hist: pd.DataFrame | None,
         symbol: str = "ETHUSDT") -> dict:
    snap: dict = {
        "symbol": symbol,
        "history": history,
        "price": float(history["close"].iloc[-1]),
    }
    if btc_hist is not None:
        snap["universe_ohlcv"] = {"BTCUSDT": btc_hist}
    return {"ts": history.index[-1], "market_snapshot": snap, "factors": {}}


# ── detect_cross 규약 (daemon 미러) ──────────────────────────────────────────


class TestDetectCross:
    def test_golden(self):
        assert detect_cross(_golden_cross_frame()["close"]) == "golden"

    def test_death(self):
        assert detect_cross(_death_cross_frame()["close"]) == "death"

    def test_no_cross_flat(self):
        assert detect_cross(_frame_from_closes(np.full(_N, 100.0))["close"]) is None

    def test_warmup_returns_none(self):
        short = _frame_from_closes(np.full(_SLOW, 100.0))["close"]
        assert detect_cross(short) is None


# ── on_bar — 레짐 게이트 결합 ────────────────────────────────────────────────


class TestInheritance:
    def test_is_live_scanner(self):
        s = LiveMacrossRegime()
        assert isinstance(s, LiveScannerMixin)
        assert s.is_live_scanner is True

    def test_stop_tp_classvars(self):
        s = LiveMacrossRegime()
        assert s.stop_loss_pct == 0.02
        assert s.take_profit_pct == 0.12

    def test_shorts_allowed(self):
        assert LiveMacrossRegime.shorts_allowed is True

    def test_interval(self):
        assert LiveMacrossRegime.get_interval() == "1h"


class TestRegimeGate:
    def test_a_golden_uptrend_buys(self):
        # trending 프레임: slope + ADX + BTC 레짐 모두 통과 → buy.
        sig = _run(LiveMacrossRegime(),
                   _ctx(_golden_cross_trending_frame(), _btc_frame("up")))
        assert sig.action == "buy"
        assert "macross_golden_long" in sig.reason

    def test_b_death_downtrend_sells(self):
        sig = _run(LiveMacrossRegime(),
                   _ctx(_death_cross_trending_frame(), _btc_frame("down")))
        assert sig.action == "sell"
        assert "macross_death_short" in sig.reason

    def test_c_golden_downtrend_holds(self):
        # trending 골든크로스 + BTC 하락장 → regime_gate hold.
        sig = _run(LiveMacrossRegime(),
                   _ctx(_golden_cross_trending_frame(), _btc_frame("down")))
        assert sig.action == "hold"
        assert "regime_gate" in sig.reason

    def test_d_death_uptrend_holds(self):
        # trending 데드크로스 + BTC 상승장 → regime_gate hold.
        sig = _run(LiveMacrossRegime(),
                   _ctx(_death_cross_trending_frame(), _btc_frame("up")))
        assert sig.action == "hold"
        assert "regime_gate" in sig.reason

    def test_e_warmup_holds(self):
        short = _frame_from_closes(np.full(_SLOW, 100.0))  # 200 < 202
        sig = _run(LiveMacrossRegime(), _ctx(short, _btc_frame("up")))
        assert sig.action == "hold"
        assert sig.reason == "warmup"

    def test_f_no_btc_data_holds(self):
        # universe_ohlcv 자체 부재 → 보수적 skip.
        # slope + ADX 는 통과하는 trending 프레임 사용.
        sig = _run(LiveMacrossRegime(),
                   _ctx(_golden_cross_trending_frame(), None))
        assert sig.action == "hold"
        assert sig.reason == "btc_regime_unavailable"

    def test_f2_btc_warmup_holds(self):
        # BTC 봉 부족 (regime 판정 불가) → 보수적 skip.
        short_btc = _frame_from_closes(np.linspace(20000.0, 40000.0, _SLOW - 1))
        sig = _run(LiveMacrossRegime(),
                   _ctx(_golden_cross_trending_frame(), short_btc))
        assert sig.action == "hold"
        assert sig.reason == "btc_regime_unavailable"

    def test_no_cross_holds(self):
        flat = _frame_from_closes(np.full(_N, 100.0))
        sig = _run(LiveMacrossRegime(), _ctx(flat, _btc_frame("up")))
        assert sig.action == "hold"
        assert sig.reason == "no_cross"


# ── _slow_slope 단위 테스트 ────────────────────────────────────────────────────


class TestSlowSlope:
    def test_upward_slope(self):
        closes = pd.Series(np.linspace(80.0, 120.0, _N))
        assert _slow_slope(closes) == "up"

    def test_downward_slope(self):
        closes = pd.Series(np.linspace(120.0, 80.0, _N))
        assert _slow_slope(closes) == "down"

    def test_flat_slope(self):
        closes = pd.Series(np.full(_N, 100.0))
        assert _slow_slope(closes) == "flat"

    def test_too_short_returns_none(self):
        closes = pd.Series(np.linspace(80.0, 120.0, _SLOW))  # _SLOW + lookback 미달
        assert _slow_slope(closes) is None


# ── slope 필터 on_bar 통합 테스트 ────────────────────────────────────────────


class TestSlopeFilter:
    def test_golden_cross_flat_sma_holds(self):
        """골든크로스이지만 SMA200 flat 또는 ADX 낮음 → hold.

        _golden_cross_frame() 은 flat(100) baseline 위에 마지막 봉 +25 급등.
        SMA200 은 거의 flat 이지만 마지막 봉 급등이 SMA200[-1] 을 미세하게
        올릴 수 있어 slope="up" 으로 통과할 수도 있다. 어느 경우든 flat
        baseline 의 ATR 이 작아 ADX < 20 → adx_gate 에서 반드시 hold.
        따라서 slope_gate 또는 adx_gate 중 하나에서 hold 가 나와야 한다.
        """
        sig = _run(LiveMacrossRegime(),
                   _ctx(_golden_cross_frame(), _btc_frame("up")))
        assert sig.action == "hold"
        assert "slope_gate" in sig.reason or "adx_gate" in sig.reason

    def test_death_cross_flat_sma_holds(self):
        """데드크로스이지만 SMA200 flat 또는 ADX 낮음 → hold."""
        sig = _run(LiveMacrossRegime(),
                   _ctx(_death_cross_frame(), _btc_frame("down")))
        assert sig.action == "hold"
        assert "slope_gate" in sig.reason or "adx_gate" in sig.reason

    def test_golden_cross_trending_up_buys(self):
        """골든크로스 + SMA200 상향 + BTC 상승장 → buy."""
        sig = _run(LiveMacrossRegime(),
                   _ctx(_golden_cross_trending_frame(), _btc_frame("up")))
        assert sig.action == "buy"
        assert "macross_golden_long" in sig.reason

    def test_death_cross_trending_down_sells(self):
        """데드크로스 + SMA200 하향 + BTC 하락장 → sell."""
        sig = _run(LiveMacrossRegime(),
                   _ctx(_death_cross_trending_frame(), _btc_frame("down")))
        assert sig.action == "sell"
        assert "macross_death_short" in sig.reason

    def test_golden_cross_downward_slope_holds(self):
        """골든크로스인데 SMA200 flat → slope_gate hold.

        _golden_cross_frame() 은 flat baseline(100) → SMA200 도 flat →
        slope_gate 또는 adx_gate 중 먼저 걸리는 쪽에서 hold.
        ADX 도 flat 이라 낮으므로 둘 중 하나가 hold 를 내보냄을 확인.
        """
        sig = _run(LiveMacrossRegime(),
                   _ctx(_golden_cross_frame(), _btc_frame("up")))
        assert sig.action == "hold"
        assert sig.reason in (
            "slope_gate:golden_slope=flat",
            f"adx_gate:adx={sig.reason.split('=')[1] if 'adx_gate' in sig.reason else ''}",
        ) or "slope_gate" in sig.reason or "adx_gate" in sig.reason


# ── ADX 필터 on_bar 통합 테스트 ──────────────────────────────────────────────


class TestADXFilter:
    def test_adx_warmup_holds(self):
        """MIN_HISTORY(202봉) 미달 → warmup hold (ADX 계산 전에 걸림)."""
        short = _frame_from_closes(np.full(_SLOW, 100.0))  # 200 < 202
        sig = _run(LiveMacrossRegime(), _ctx(short, _btc_frame("up")))
        assert sig.action == "hold"
        assert sig.reason == "warmup"

    def test_ranging_frame_blocked(self):
        """flat close → ATR≈0, ADX≈0 → slope_gate 또는 adx_gate 에서 hold.

        flat 프레임은 크로스도 거의 없지만 _golden_cross_frame() 은
        마지막 1봉 급등으로 크로스를 유발한다. SMA200 은 flat 이므로
        slope_gate 에 먼저 걸리거나 ADX<20 으로 adx_gate 에 걸린다.
        어느 쪽이든 hold 가 나와야 함 — ranging 환경 차단이 목적.
        """
        sig = _run(LiveMacrossRegime(),
                   _ctx(_golden_cross_frame(), _btc_frame("up")))
        assert sig.action == "hold"
        assert "slope_gate" in sig.reason or "adx_gate" in sig.reason

    def test_trending_frame_passes_adx(self):
        """지그재그 상승 프레임은 ADX≥20 을 충족해 adx_gate 를 통과한다.

        slope + ADX + BTC 레짐 모두 통과 → buy.
        이 테스트가 pass 하면 trending 프레임 설계가 올바름을 증명.
        """
        sig = _run(LiveMacrossRegime(),
                   _ctx(_golden_cross_trending_frame(), _btc_frame("up")))
        assert sig.action == "buy", (
            f"Expected buy but got hold: {sig.reason}. "
            "trending 프레임의 ADX 가 20 미만 — 프레임 설계 재검토 필요."
        )


# ── 리서치 confluence 필터 (opt-in, 숏-집중 스택) ─────────────────────────────


def _gentle_death_frame() -> pd.DataFrame:
    """데드크로스 + close 양수(105) + close<SMA200 + 과확장 아님 + slope↓ + ADX≥20.

    close 가 110 flat → 마지막 105 (X<110 이면 데드크로스, math: 7X<770).
    SMA200[-1]=(199×110+105)/200=109.975 → close(105)<sma200 (자기200 정렬 ✓),
    ext=(109.975-105)/105=4.7% (<10% 과확장 아님 ✓). lows 매봉 하향 → -DM 누적.
    """
    n = _N
    base, spike = 110.0, 105.0
    closes = np.full(n, base)
    closes[-1] = spike
    highs = np.full(n, base + 1.0)
    lows = np.array([base - i * 0.05 - 10.0 for i in range(n)])
    highs[-1] = spike + 1.0
    lows[-1] = spike - 1.0
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows,
         "close": closes, "volume": np.full(n, 1000.0)},
        index=idx,
    )


def _reindex_end_kst(df: pd.DataFrame, kst_hour: int) -> pd.DataFrame:
    """마지막 봉의 KST 시(時) 가 kst_hour 가 되도록 인덱스 재배치."""
    utc_hour = (kst_hour - 9) % 24
    end = pd.Timestamp("2024-01-09", tz="UTC") + pd.Timedelta(hours=utc_hour)
    out = df.copy()
    out.index = pd.date_range(end=end, periods=len(df), freq="1h", tz="UTC")
    return out


class TestConfluenceFilters:
    def test_both_directions_disabled_raises(self):
        try:
            LiveMacrossRegime(allow_long=False, allow_short=False)
        except ValueError:
            return
        raise AssertionError("allow_long·allow_short 둘 다 False → ValueError 기대")

    def test_allow_long_false_blocks_golden(self):
        # 숏-집중: 골든크로스(롱 후보)는 long_disabled 로 hold.
        sig = _run(LiveMacrossRegime(allow_long=False),
                   _ctx(_golden_cross_trending_frame(), _btc_frame("up")))
        assert sig.action == "hold"
        assert "long_disabled" in sig.reason

    def test_allow_short_false_blocks_death(self):
        sig = _run(LiveMacrossRegime(allow_short=False),
                   _ctx(_death_cross_trending_frame(), _btc_frame("down")))
        assert sig.action == "hold"
        assert "short_disabled" in sig.reason

    def test_kst_gate_blocks_offhour(self):
        # KST 21시(게이트 밖)에 끝나는 데드크로스 → kst_gate 로 hold.
        df = _reindex_end_kst(_gentle_death_frame(), 21)
        sig = _run(LiveMacrossRegime(kst_hour_gate=True),
                   _ctx(df, _btc_frame("down")))
        assert sig.action == "hold"
        assert "kst_gate" in sig.reason

    def test_kst_gate_allows_ingate(self):
        # KST 22시(자체도출 게이트 안 {2,3,4,5,6,7,12,13,14,19,22}) → 통과해서 sell.
        df = _reindex_end_kst(_gentle_death_frame(), 22)
        sig = _run(LiveMacrossRegime(kst_hour_gate=True),
                   _ctx(df, _btc_frame("down")))
        assert sig.action == "sell"

    def test_kst_gate_blocks_outgate(self):
        # KST 23시(자체도출 게이트 밖 — 옛 에어본차용은 in이었음) → hold.
        df = _reindex_end_kst(_gentle_death_frame(), 23)
        sig = _run(LiveMacrossRegime(kst_hour_gate=True),
                   _ctx(df, _btc_frame("down")))
        assert sig.action == "hold"
        assert "kst_gate" in sig.reason

    def test_self_sma200_aligned_short_passes(self):
        sig = _run(LiveMacrossRegime(self_sma200_filter=True),
                   _ctx(_gentle_death_frame(), _btc_frame("down")))
        assert sig.action == "sell"

    def test_overextension_blocks_far_entry(self):
        # 골든 trending: close(200) 가 SMA200(~90.6) 에서 +50%↑ → overextended hold.
        sig = _run(LiveMacrossRegime(overextension_max_pct=0.10),
                   _ctx(_golden_cross_trending_frame(), _btc_frame("up")))
        assert sig.action == "hold"
        assert "overextended" in sig.reason

    def test_full_short_stack_sells(self):
        # 권장 숏-집중 풀스택: allow_long=False + 시간게이트(22시=in) + 자기200 + 과확장.
        df = _reindex_end_kst(_gentle_death_frame(), 22)
        s = LiveMacrossRegime(
            allow_long=False, kst_hour_gate=True,
            self_sma200_filter=True, overextension_max_pct=0.10)
        sig = _run(s, _ctx(df, _btc_frame("down")))
        assert sig.action == "sell"
        assert "macross_death_short" in sig.reason

    def test_default_filters_off_unchanged(self):
        # 기본(필터 OFF) 동작 보존 — 골든 trending+up → buy.
        sig = _run(LiveMacrossRegime(),
                   _ctx(_golden_cross_trending_frame(), _btc_frame("up")))
        assert sig.action == "buy"
