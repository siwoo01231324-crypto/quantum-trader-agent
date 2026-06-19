"""Unit tests for LivePppScalping (PPP 스캘핑: EMA 배열 + 이평 지지 + QPP 크로스).

strategy id: live-ppp-scalping-v1 (id-snake: live_ppp_scalping_v1).
module: backtest.strategies.live_ppp_scalping_v1.LivePppScalping.

컴포넌트(stoch_rsi/qpp_cross/_touched_recent)는 합성 데이터로 직접 검증,
on_bar 결정로직(1P 레짐 게이트 / 3P 크로스 / 2P 지지)은 monkeypatch 로 격리 검증.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd

import backtest.strategies.live_ppp_scalping_v1 as mod
from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_ppp_scalping_v1 import (
    LivePppScalping,
    qpp_cross,
    stoch_rsi,
    _ema,
    _touched_recent,
)

_N = 260  # MIN_HISTORY(242) 통과


def _run(strategy, ctx: dict) -> Signal | None:
    return asyncio.run(strategy.on_bar(ctx))


def _frame(closes: np.ndarray, *, lows=None, highs=None) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + 0.5 if highs is None else highs,
            "low": closes - 0.5 if lows is None else lows,
            "close": closes,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def _ctx(history: pd.DataFrame, symbol: str = "ETHUSDT") -> dict:
    snap = {"symbol": symbol, "history": history,
            "price": float(history["close"].iloc[-1])}
    return {"ts": history.index[-1], "market_snapshot": snap, "factors": {}}


def _osc(trend_start: float, trend_end: float, n: int = 700,
         amp: float = 4.0, period: int = 18) -> np.ndarray:
    t = np.arange(n)
    return np.linspace(trend_start, trend_end, n) + amp * np.sin(2 * np.pi * t / period)


def _slice_to_last_cross(closes: np.ndarray, kind: str) -> pd.DataFrame:
    """마지막 봉이 정확히 golden/death 크로스가 되도록 시계열을 자른다."""
    h = _frame(closes)
    main, sig = stoch_rsi(h["close"])
    if kind == "golden":
        cond = (main.shift(1) <= sig.shift(1)) & (main > sig)
    else:
        cond = (main.shift(1) >= sig.shift(1)) & (main < sig)
    idxs = np.where(cond.fillna(False).values)[0]
    idxs = idxs[idxs >= 250]
    assert len(idxs) > 0, f"no {kind} cross found in synthetic series"
    cut = int(idxs[-1])
    return h.iloc[: cut + 1]


# ── 컴포넌트: stoch_rsi / qpp_cross ──────────────────────────────────────────

class TestQppCross:
    def test_golden_detected_at_last_bar(self):
        h = _slice_to_last_cross(_osc(100.0, 160.0), "golden")
        assert qpp_cross(h["close"]) == "golden"

    def test_death_detected_at_last_bar(self):
        h = _slice_to_last_cross(_osc(160.0, 100.0), "death")
        assert qpp_cross(h["close"]) == "death"

    def test_flat_no_cross(self):
        assert qpp_cross(_frame(np.full(_N, 100.0))["close"]) is None

    def test_warmup_none(self):
        assert qpp_cross(_frame(np.full(30, 100.0))["close"]) is None

    def test_stoch_rsi_bounded_0_100(self):
        main, _ = stoch_rsi(_frame(_osc(100.0, 160.0))["close"])
        m = main.dropna()
        assert (m >= -1e-9).all() and (m <= 100 + 1e-9).all()


# ── 컴포넌트: _touched_recent ────────────────────────────────────────────────

class TestTouch:
    def test_support_touch_true(self):
        # close 가 ema 위, low 가 ema 에 닿음.
        closes = np.linspace(100.0, 120.0, _N)
        h = _frame(closes)
        emas = [_ema(h["close"], p) for p in (60, 120, 240)]
        # 마지막 봉 low 를 ema60 에 인위적으로 닿게.
        e60_last = emas[0].iloc[-1]
        h.loc[h.index[-1], "low"] = float(e60_last)
        h.loc[h.index[-1], "close"] = float(e60_last) + 1.0
        assert _touched_recent(h, emas, tol=0.01, lookback=3, side="long") is True

    def test_no_touch_when_far(self):
        closes = np.linspace(100.0, 120.0, _N)
        h = _frame(closes, lows=closes + 5.0)  # low 가 ema 보다 한참 위
        emas = [_ema(h["close"], p) for p in (60, 120, 240)]
        assert _touched_recent(h, emas, tol=0.001, lookback=3, side="long") is False


# ── 상속/속성 ────────────────────────────────────────────────────────────────

class TestInheritance:
    def test_is_live_scanner(self):
        assert LivePppScalping().is_live_scanner is True
        assert isinstance(LivePppScalping(), LiveScannerMixin)

    def test_stop_tp_classvars(self):
        s = LivePppScalping()
        assert s.stop_loss_pct == 0.015
        assert s.take_profit_pct == 0.03

    def test_shorts_allowed(self):
        assert LivePppScalping.shorts_allowed is True

    def test_interval(self):
        assert LivePppScalping.get_interval() == "15m"


# ── on_bar 결정로직 (monkeypatch 격리) ───────────────────────────────────────

def _bull(n: int = _N) -> pd.DataFrame:
    return _frame(np.linspace(100.0, 150.0, n))   # ema120>ema240

def _bear(n: int = _N) -> pd.DataFrame:
    return _frame(np.linspace(150.0, 100.0, n))   # ema120<ema240


class TestOnBar:
    def test_warmup_holds(self):
        sig = _run(LivePppScalping(), _ctx(_frame(np.full(200, 100.0))))
        assert sig.action == "hold" and sig.reason == "warmup"

    def test_flat_no_cross_holds(self):
        sig = _run(LivePppScalping(), _ctx(_frame(np.full(_N, 100.0))))
        assert sig.action == "hold" and sig.reason == "no_qpp_cross"

    def test_long_path_buys(self, monkeypatch):
        monkeypatch.setattr(mod, "qpp_cross", lambda *a, **k: "golden")
        monkeypatch.setattr(mod, "_touched_recent", lambda *a, **k: True)
        sig = _run(LivePppScalping(), _ctx(_bull()))
        assert sig.action == "buy" and "ppp_long" in sig.reason

    def test_short_path_sells(self, monkeypatch):
        monkeypatch.setattr(mod, "qpp_cross", lambda *a, **k: "death")
        monkeypatch.setattr(mod, "_touched_recent", lambda *a, **k: True)
        sig = _run(LivePppScalping(), _ctx(_bear()))
        assert sig.action == "sell" and "ppp_short" in sig.reason

    def test_golden_in_bear_regime_holds(self, monkeypatch):
        monkeypatch.setattr(mod, "qpp_cross", lambda *a, **k: "golden")
        monkeypatch.setattr(mod, "_touched_recent", lambda *a, **k: True)
        sig = _run(LivePppScalping(), _ctx(_bear()))
        assert sig.action == "hold" and "regime_gate:golden_not_bull" in sig.reason

    def test_long_no_support_holds(self, monkeypatch):
        monkeypatch.setattr(mod, "qpp_cross", lambda *a, **k: "golden")
        monkeypatch.setattr(mod, "_touched_recent", lambda *a, **k: False)
        sig = _run(LivePppScalping(), _ctx(_bull()))
        assert sig.action == "hold" and sig.reason == "no_ema_support"

    def test_long_disabled_holds(self, monkeypatch):
        monkeypatch.setattr(mod, "qpp_cross", lambda *a, **k: "golden")
        monkeypatch.setattr(mod, "_touched_recent", lambda *a, **k: True)
        sig = _run(LivePppScalping(allow_long=False), _ctx(_bull()))
        assert sig.action == "hold" and sig.reason == "long_disabled"

    def test_btc_regime_gate_blocks(self, monkeypatch):
        # btc_regime_gate=True 인데 universe_ohlcv 부재 → 보수적 hold.
        monkeypatch.setattr(mod, "qpp_cross", lambda *a, **k: "golden")
        monkeypatch.setattr(mod, "_touched_recent", lambda *a, **k: True)
        sig = _run(LivePppScalping(btc_regime_gate=True), _ctx(_bull()))
        assert sig.action == "hold" and sig.reason == "btc_regime_unavailable"


def _div_series(label):
    return lambda close, rsi, lb: pd.Series([label] * len(close), index=close.index)


def _stoch_const(mainval):
    """stoch_rsi monkeypatch — 본선/시그널 마지막값 고정 (zone 제어)."""
    def _f(close, **k):
        s = pd.Series([float(mainval)] * len(close), index=close.index)
        return s, s
    return _f


class TestConfluence4PZone:
    """4P 다이버전스 + OB/OS 구간 가산점 → confidence 0.5/0.65/0.8."""

    def _patch(self, monkeypatch, cross, div, mainval):
        monkeypatch.setattr(mod, "qpp_cross", lambda *a, **k: cross)
        monkeypatch.setattr(mod, "_touched_recent", lambda *a, **k: True)
        monkeypatch.setattr(mod, "detect_divergence", _div_series(div))
        monkeypatch.setattr(mod, "stoch_rsi", _stoch_const(mainval))

    def test_full_confluence_conf_080(self, monkeypatch):
        self._patch(monkeypatch, "golden", "bullish", 20)  # div + 과매도
        sig = _run(LivePppScalping(), _ctx(_bull()))
        assert sig.action == "buy"
        assert abs(sig.confidence - 0.8) < 1e-9
        assert "4P=bullish" in sig.reason and "zone=20" in sig.reason

    def test_div_only_conf_065(self, monkeypatch):
        self._patch(monkeypatch, "golden", "bullish", 90)  # div only
        sig = _run(LivePppScalping(), _ctx(_bull()))
        assert abs(sig.confidence - 0.65) < 1e-9

    def test_zone_only_conf_065(self, monkeypatch):
        self._patch(monkeypatch, "golden", None, 20)  # zone only
        sig = _run(LivePppScalping(), _ctx(_bull()))
        assert abs(sig.confidence - 0.65) < 1e-9

    def test_neither_conf_050(self, monkeypatch):
        self._patch(monkeypatch, "golden", None, 90)
        sig = _run(LivePppScalping(), _ctx(_bull()))
        assert abs(sig.confidence - 0.5) < 1e-9
        assert "4P=none" in sig.reason

    def test_require_divergence_blocks(self, monkeypatch):
        self._patch(monkeypatch, "golden", None, 20)
        sig = _run(LivePppScalping(require_divergence=True), _ctx(_bull()))
        assert sig.action == "hold" and sig.reason.startswith("no_divergence")

    def test_require_zone_blocks(self, monkeypatch):
        self._patch(monkeypatch, "golden", "bullish", 90)  # 과매도 아님
        sig = _run(LivePppScalping(require_zone=True), _ctx(_bull()))
        assert sig.action == "hold" and sig.reason.startswith("no_zone")

    def test_require_zone_allows(self, monkeypatch):
        self._patch(monkeypatch, "golden", "bullish", 20)
        sig = _run(LivePppScalping(require_zone=True), _ctx(_bull()))
        assert sig.action == "buy"

    def test_short_requires_bearish_divergence(self, monkeypatch):
        # bullish 다이버전스는 숏 방향과 불일치 → require 시 차단 (페이크 필터).
        self._patch(monkeypatch, "death", "bullish", 80)
        sig = _run(LivePppScalping(require_divergence=True), _ctx(_bear()))
        assert sig.action == "hold" and sig.reason.startswith("no_divergence")

    def test_short_overbought_zone(self, monkeypatch):
        # 숏: 과매수(>75) 구간 데크 + bearish div → buy(sell) conf 0.8.
        self._patch(monkeypatch, "death", "bearish", 85)
        sig = _run(LivePppScalping(), _ctx(_bear()))
        assert sig.action == "sell"
        assert abs(sig.confidence - 0.8) < 1e-9
        assert "zone=85" in sig.reason


class TestExitModes:
    """sl_mode/tp_mode 별 per-entry 청산 (Signal override) — 오케스트레이터 수정 0."""

    def test_default_fixed_no_override(self, monkeypatch):
        monkeypatch.setattr(mod, "qpp_cross", lambda *a, **k: "golden")
        monkeypatch.setattr(mod, "_touched_recent", lambda *a, **k: True)
        sig = _run(LivePppScalping(), _ctx(_bull()))  # fixed/fixed
        assert sig.action == "buy"
        assert sig.stop_loss_pct_override is None
        assert sig.take_profit_pct_override is None

    def test_sl_ema_sets_stop(self):
        s = LivePppScalping(sl_mode="ema")
        close = pd.Series(np.linspace(100.0, 150.0, 260))
        stop, tp = s._exit_overrides(close, 150.0, [140.0, 130.0, 120.0], "long")
        assert stop is not None and stop >= 0.002
        assert tp is None  # tp_mode=fixed

    def test_tp_next_ema(self):
        s = LivePppScalping(tp_mode="next_ema")
        close = pd.Series(np.linspace(100.0, 150.0, 260))
        stop, tp = s._exit_overrides(close, 150.0, [148.0, 152.0, 160.0], "long")
        assert tp is not None and abs(tp - (152.0 - 150.0) / 150.0) < 1e-9
        assert stop is None  # sl_mode=fixed

    def test_tp_bb_mid(self):
        s = LivePppScalping(tp_mode="bb_mid", bb_period=20)
        close = pd.Series([100.0] * 19 + [95.0])  # bb_mid ~99.75 > c_now 95
        _, tp = s._exit_overrides(close, 95.0, [94.0, 93.0, 92.0], "long")
        assert tp is not None and tp > 0

    def test_tp_bb_upper(self):
        s = LivePppScalping(tp_mode="bb_upper", bb_period=20, bb_std=2.0)
        close = pd.Series([100.0] * 19 + [95.0])
        _, tp = s._exit_overrides(close, 95.0, [94.0], "long")
        assert tp is not None and tp > 0

    def test_invalid_modes_raise(self):
        import pytest
        with pytest.raises(ValueError):
            LivePppScalping(sl_mode="nope")
        with pytest.raises(ValueError):
            LivePppScalping(tp_mode="nope")

    def test_ema_sl_wired_in_on_bar(self, monkeypatch):
        monkeypatch.setattr(mod, "qpp_cross", lambda *a, **k: "golden")
        monkeypatch.setattr(mod, "_touched_recent", lambda *a, **k: True)
        sig = _run(LivePppScalping(sl_mode="ema"), _ctx(_bull()))
        assert sig.action == "buy"
        assert sig.stop_loss_pct_override is not None and sig.stop_loss_pct_override >= 0.002
