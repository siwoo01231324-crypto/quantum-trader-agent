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
    """
    closes = np.full(_N, 100.0)
    closes[-1] = 100.0 + 1.0 * _FAST  # fast 평균을 +1 끌어올려 slow 추월
    return _frame_from_closes(closes)


def _death_cross_frame() -> pd.DataFrame:
    """마지막 확정봉에서 fast SMA 가 slow SMA 를 하향 돌파하도록 구성 (mirror)."""
    closes = np.full(_N, 100.0)
    closes[-1] = 100.0 - 1.0 * _FAST  # fast 평균을 -1 끌어내려 slow 하향 돌파
    return _frame_from_closes(closes)


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
        sig = _run(LiveMacrossRegime(),
                   _ctx(_golden_cross_frame(), _btc_frame("up")))
        assert sig.action == "buy"
        assert "macross_golden_long" in sig.reason

    def test_b_death_downtrend_sells(self):
        sig = _run(LiveMacrossRegime(),
                   _ctx(_death_cross_frame(), _btc_frame("down")))
        assert sig.action == "sell"
        assert "macross_death_short" in sig.reason

    def test_c_golden_downtrend_holds(self):
        sig = _run(LiveMacrossRegime(),
                   _ctx(_golden_cross_frame(), _btc_frame("down")))
        assert sig.action == "hold"
        assert "regime_gate" in sig.reason

    def test_d_death_uptrend_holds(self):
        sig = _run(LiveMacrossRegime(),
                   _ctx(_death_cross_frame(), _btc_frame("up")))
        assert sig.action == "hold"
        assert "regime_gate" in sig.reason

    def test_e_warmup_holds(self):
        short = _frame_from_closes(np.full(_SLOW, 100.0))  # 200 < 202
        sig = _run(LiveMacrossRegime(), _ctx(short, _btc_frame("up")))
        assert sig.action == "hold"
        assert sig.reason == "warmup"

    def test_f_no_btc_data_holds(self):
        # universe_ohlcv 자체 부재 → 보수적 skip.
        sig = _run(LiveMacrossRegime(),
                   _ctx(_golden_cross_frame(), None))
        assert sig.action == "hold"
        assert sig.reason == "btc_regime_unavailable"

    def test_f2_btc_warmup_holds(self):
        # BTC 봉 부족 (regime 판정 불가) → 보수적 skip.
        short_btc = _frame_from_closes(np.linspace(20000.0, 40000.0, _SLOW - 1))
        sig = _run(LiveMacrossRegime(),
                   _ctx(_golden_cross_frame(), short_btc))
        assert sig.action == "hold"
        assert sig.reason == "btc_regime_unavailable"

    def test_no_cross_holds(self):
        flat = _frame_from_closes(np.full(_N, 100.0))
        sig = _run(LiveMacrossRegime(), _ctx(flat, _btc_frame("up")))
        assert sig.action == "hold"
        assert sig.reason == "no_cross"
