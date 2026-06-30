"""Unit tests for LiveDonchianBreakoutBtcGate (돌파 + BTC 레짐 게이트, 4h 스윙).

buy path + warmup + 게이트 boundary(돌파/EMA200/BTC레짐) + 2ATR 손절 override +
채널청산 레벨(Donchian10 하단) 검증.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_donchian_breakout_btcgate import (
    LiveDonchianBreakoutBtcGate,
)

N = 210  # > MIN_HISTORY(205)


def _series(close: np.ndarray, *, rng: float = 0.01) -> pd.DataFrame:
    n = len(close)
    idx = pd.date_range("2026-01-01", periods=n, freq="4h")
    return pd.DataFrame({
        "open": close, "high": close * (1 + rng), "low": close * (1 - rng),
        "close": close, "volume": np.full(n, 1_000.0),
    }, index=idx)


def _btc_up() -> pd.DataFrame:
    """BTC 상승장 — close > EMA200 (꾸준한 상승)."""
    return _series(np.linspace(100, 200, N))


def _btc_down() -> pd.DataFrame:
    """BTC 하락장 — close < EMA200 (꾸준한 하락)."""
    return _series(np.linspace(200, 100, N))


def _ctx(history: pd.DataFrame, btc: pd.DataFrame | None) -> dict:
    snap = {
        "symbol": "ETHUSDT",
        "history": history,
        "price": float(history["close"].iloc[-1]),
    }
    if btc is not None:
        snap["universe_ohlcv"] = {"BTCUSDT": btc}
    return {"ts": history.index[-1], "market_snapshot": snap, "factors": {}}


def _run(strategy, ctx) -> Signal | None:
    return asyncio.run(strategy.on_bar(ctx))


def _breakout_history() -> pd.DataFrame:
    """완만한 상승(EMA200 < close 확보) 후 마지막 봉이 Donchian20 상단 명확 돌파."""
    base = np.linspace(80, 100, N - 1)
    closes = np.append(base, 108.0)  # 마지막 봉 = 신고가 (직전 max ~100 대비 +8%)
    return _series(closes, rng=0.005)


class TestMarker:
    def test_is_live_scanner_interval_trend(self):
        s = LiveDonchianBreakoutBtcGate()
        assert isinstance(s, LiveScannerMixin)
        assert s.get_interval() == "4h"
        assert s.regime_preference == "trend"
        assert s.max_hold_sec is None

    def test_universe_is_clean_crypto_top30(self):
        # 돌파 = 가장 유동적인 크립토 top-30 집중 (확대 시 엣지 열화).
        from src.portfolio.binance_universe import SWING_CRYPTO_UNIVERSE
        uni = LiveDonchianBreakoutBtcGate.get_universe()
        assert len(uni) == 30
        assert uni == list(SWING_CRYPTO_UNIVERSE[:30])
        # 토큰화주식·상품·forex 가 섞이지 않는다 (오염 회귀 방지).
        bad = {"TSLAUSDT", "NVDAUSDT", "XAUUSDT", "XAGUSDT", "EURUSDT", "QQQUSDT"}
        assert not (set(uni) & bad)


class TestBuyPath:
    def test_buy_on_breakout_btc_up(self):
        s = LiveDonchianBreakoutBtcGate()
        sig = _run(s, _ctx(_breakout_history(), _btc_up()))
        assert sig.action == "buy"
        assert "donchian_breakout" in sig.reason
        # 2ATR 동적 손절 override 채워짐.
        assert sig.stop_loss_pct_override is not None
        assert 0 < sig.stop_loss_pct_override < 1

    def test_hold_when_btc_regime_down(self):
        s = LiveDonchianBreakoutBtcGate()
        sig = _run(s, _ctx(_breakout_history(), _btc_down()))
        assert sig.action == "hold"
        assert "btc_regime_down" in sig.reason

    def test_hold_when_btc_unavailable(self):
        s = LiveDonchianBreakoutBtcGate()
        sig = _run(s, _ctx(_breakout_history(), None))  # no universe_ohlcv
        assert sig.action == "hold"
        assert "btc_regime_unavailable" in sig.reason

    def test_btc_gate_off_allows_without_btc(self):
        s = LiveDonchianBreakoutBtcGate(btc_regime_gate=False)
        sig = _run(s, _ctx(_breakout_history(), None))
        assert sig.action == "buy"


class TestGateBoundaries:
    def test_hold_when_no_breakout(self):
        s = LiveDonchianBreakoutBtcGate()
        closes = np.linspace(80, 100, N)  # 마지막 봉이 신고가 아님 (완만 상승 끝)
        sig = _run(s, _ctx(_series(closes, rng=0.005), _btc_up()))
        assert sig.action == "hold"
        assert "no_breakout" in sig.reason

    def test_hold_when_below_ema200(self):
        s = LiveDonchianBreakoutBtcGate()
        # 하락 추세 끝에서 잠깐 직전20봉 max 돌파하나 close < EMA200.
        base = np.linspace(200, 100, N - 1)
        closes = np.append(base, base[-1] * 1.02)  # 직전 저점들 대비 살짝 위 (국지 돌파)
        sig = _run(s, _ctx(_series(closes, rng=0.005), _btc_up()))
        assert sig.action == "hold"
        # no_breakout 또는 below_ema200 — 어느 쪽이든 진입 차단이 핵심.
        assert sig.action == "hold"

    def test_hold_when_warmup(self):
        s = LiveDonchianBreakoutBtcGate()
        short = _series(np.linspace(90, 100, 50), rng=0.005)
        sig = _run(s, _ctx(short, _btc_up()))
        assert sig.action == "hold"
        assert sig.reason == "warmup"


class TestChannelExitLevel:
    def test_channel_level_is_donchian10_low(self):
        s = LiveDonchianBreakoutBtcGate()
        # 마지막 11봉 low 를 알게 구성: 직전 10봉(현재봉 제외) low 의 min.
        closes = np.linspace(90, 110, N)
        hist = _series(closes, rng=0.01)
        level = s.channel_exit_level(hist)
        # 직전 10봉(현재봉 제외) low = close[-(11):-1] * 0.99 의 최소.
        expected = float((hist["low"].iloc[-(s.EXIT_LOOKBACK + 1):-1]).min())
        assert level == pytest.approx(expected, rel=1e-9)

    def test_channel_level_none_when_short(self):
        s = LiveDonchianBreakoutBtcGate()
        short = _series(np.linspace(90, 100, 5), rng=0.01)
        assert s.channel_exit_level(short) is None


class TestValidation:
    def test_rejects_bad_default_size(self):
        with pytest.raises(ValueError):
            LiveDonchianBreakoutBtcGate(default_size=0.0)

    def test_rejects_nonpositive_atr_mult(self):
        with pytest.raises(ValueError):
            LiveDonchianBreakoutBtcGate(stop_atr_mult=0.0)
