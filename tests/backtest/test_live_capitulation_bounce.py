"""Unit tests for LiveCapitulationBounce (투매반등 평균회귀, 4h 스윙).

리서치 종결(2026-06-25) 채택 신호. buy path + warmup + 각 게이트 boundary +
동적 꼬리저점 stop/2R TP override 검증.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_capitulation_bounce import LiveCapitulationBounce


def _history(final: dict, *, n: int = 35, base: float = 100.0,
             base_vol: float = 1_000.0) -> pd.DataFrame:
    """flat-ish base (EMA20/ATR warmup 확보) + 커스텀 final 봉.

    base 봉: O=C=base, H=base+0.5, L=base-0.5 (TR≈1 → ATR≈1, EMA20≈base).
    final: {open, high, low, close, volume} dict 로 마지막 봉 덮어씀.
    """
    idx = pd.date_range("2026-01-01", periods=n, freq="4h")
    o = np.full(n, base); h = np.full(n, base + 0.5)
    l = np.full(n, base - 0.5); c = np.full(n, base)
    v = np.full(n, base_vol)
    o[-1] = final["open"]; h[-1] = final["high"]
    l[-1] = final["low"]; c[-1] = final["close"]; v[-1] = final["volume"]
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=idx)


def _ctx(history: pd.DataFrame) -> dict:
    return {
        "ts": history.index[-1],
        "market_snapshot": {
            "symbol": "BTCUSDT",
            "history": history,
            "price": float(history["close"].iloc[-1]),
        },
        "factors": {},
    }


def _run(strategy, ctx) -> Signal | None:
    return asyncio.run(strategy.on_bar(ctx))


# 투매 hammer: low=95 (EMA20≈100, ATR≈1.x → level≈96.x, 95<=level ✓),
# bullish (close 99.5 > open 99), 긴 아랫꼬리 (wick=99-95=4 >= 1.5×0.5=0.75),
# 거래량 스파이크 (3000 > 2×1000).
_HAMMER = {"open": 99.0, "high": 99.6, "low": 95.0, "close": 99.5, "volume": 3_000.0}


class TestMarker:
    def test_is_live_scanner_and_interval(self):
        s = LiveCapitulationBounce()
        assert isinstance(s, LiveScannerMixin)
        assert s.is_live_scanner is True
        assert s.get_interval() == "4h"

    def test_meanrev_regime_and_no_timestop(self):
        s = LiveCapitulationBounce()
        assert s.regime_preference == "meanrev"
        # 스윙 평균회귀 — time-stop 면제 (반등까지 보유).
        assert s.max_hold_sec is None

    def test_universe_is_clean_crypto_top100(self):
        # 투매반등 = 크립토 top-100 확대 (품질 유지+거래수↑). 돌파보다 넓다.
        from src.portfolio.binance_universe import SWING_CRYPTO_UNIVERSE
        uni = LiveCapitulationBounce.get_universe()
        assert uni == list(SWING_CRYPTO_UNIVERSE[:100])
        # 투매반등(top-100) ⊇ 돌파(top-30) — 확대 비대칭 검증.
        from src.backtest.strategies.live_donchian_breakout_btcgate import (
            LiveDonchianBreakoutBtcGate,
        )
        assert set(LiveDonchianBreakoutBtcGate.get_universe()) <= set(uni)
        assert len(uni) > 30
        # 토큰화주식·상품·forex 오염 회귀 방지.
        bad = {"TSLAUSDT", "NVDAUSDT", "XAUUSDT", "XAGUSDT", "EURUSDT", "QQQUSDT"}
        assert not (set(uni) & bad)


class TestBuyPath:
    def test_buy_on_capitulation_hammer(self):
        s = LiveCapitulationBounce()
        sig = _run(s, _ctx(_history(_HAMMER)))
        assert sig.action == "buy"
        assert "capitulation_bounce" in sig.reason
        assert 0.0 <= sig.confidence <= 1.0

    def test_dynamic_stop_is_wick_low_and_tp_is_2R(self):
        s = LiveCapitulationBounce()
        sig = _run(s, _ctx(_history(_HAMMER)))
        assert sig.action == "buy"
        close, low = 99.5, 95.0
        expected_sl = (close - low) / close
        assert sig.stop_loss_pct_override == pytest.approx(expected_sl, rel=1e-9)
        assert sig.take_profit_pct_override == pytest.approx(2.0 * expected_sl, rel=1e-9)

    def test_rr_kwarg_changes_tp(self):
        s = LiveCapitulationBounce(rr=3.0)
        sig = _run(s, _ctx(_history(_HAMMER)))
        close, low = 99.5, 95.0
        assert sig.take_profit_pct_override == pytest.approx(3.0 * (close - low) / close, rel=1e-9)


class TestGateBoundaries:
    def test_hold_when_not_bullish(self):
        bear = dict(_HAMMER, open=99.5, close=99.0)  # close < open
        sig = _run(LiveCapitulationBounce(), _ctx(_history(bear)))
        assert sig.action == "hold"
        assert sig.reason == "not_bullish"

    def test_hold_when_wick_too_short(self):
        # 긴 몸통, 짧은 아랫꼬리: open=96 close=99.5 body=3.5, low=95.8 wick=0.2 < 1.5×3.5
        short_wick = {"open": 96.0, "high": 99.6, "low": 95.8, "close": 99.5, "volume": 3_000.0}
        sig = _run(LiveCapitulationBounce(), _ctx(_history(short_wick)))
        assert sig.action == "hold"
        assert "wick_short" in sig.reason

    def test_hold_when_no_capitulation(self):
        # 긴 아랫꼬리 + 양봉 + 거래량 있으나 low 가 capitulation level 위 (얕은 dip).
        # body=0.2, wick=1.8(>=0.3 통과), low=98.0 > level(≈97.3) → no_capitulation.
        shallow = {"open": 99.8, "high": 100.1, "low": 98.0, "close": 100.0, "volume": 3_000.0}
        sig = _run(LiveCapitulationBounce(), _ctx(_history(shallow)))
        assert sig.action == "hold"
        assert "no_capitulation" in sig.reason

    def test_hold_when_no_volume_spike(self):
        no_vol = dict(_HAMMER, volume=1_000.0)  # == baseline, ratio 1.0 < 2.0
        sig = _run(LiveCapitulationBounce(), _ctx(_history(no_vol)))
        assert sig.action == "hold"
        assert "vol_low" in sig.reason

    def test_hold_when_warmup(self):
        hist = _history(_HAMMER, n=10)  # < MIN_HISTORY (30)
        sig = _run(LiveCapitulationBounce(), _ctx(hist))
        assert sig.action == "hold"
        assert sig.reason == "warmup"


class TestValidation:
    def test_rejects_bad_default_size(self):
        with pytest.raises(ValueError):
            LiveCapitulationBounce(default_size=0.0)
        with pytest.raises(ValueError):
            LiveCapitulationBounce(default_size=1.5)

    def test_rejects_nonpositive_params(self):
        for kw in ("n_dev", "wick_mult", "vol_mult", "rr"):
            with pytest.raises(ValueError):
                LiveCapitulationBounce(**{kw: 0.0})
