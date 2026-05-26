"""Unit tests for LiveBreakoutWithAtrStop (#227 S4)."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_breakout_with_atr_stop import LiveBreakoutWithAtrStop


def _ohlcv(closes: np.ndarray) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {
            "open": closes, "high": closes * 1.002, "low": closes * 0.998,
            "close": closes, "volume": np.full(n, 1_000.0),
        },
        index=idx,
    )


def _ctx(history: pd.DataFrame) -> dict:
    return {
        "ts": history.index[-1],
        "market_snapshot": {
            "symbol": "005930",
            "history": history,
            "price": float(history["close"].iloc[-1]),
        },
        "factors": {},
    }


def _run(strategy, ctx) -> Signal | None:
    return asyncio.run(strategy.on_bar(ctx))


class TestLiveBreakoutWithAtrStop:
    def test_marker_and_trailing_attr(self):
        s = LiveBreakoutWithAtrStop()
        assert isinstance(s, LiveScannerMixin)
        # Trailing stop is the primary exit for this strategy.
        assert s.trailing_stop_pct is not None
        assert s.trailing_stop_pct == 0.04

    def test_buy_when_new_20_bar_high(self):
        s = LiveBreakoutWithAtrStop()
        n = 40
        closes = np.linspace(100, 110, n - 1).tolist() + [115.0]  # final = new high
        history = _ohlcv(np.array(closes))
        signal = _run(s, _ctx(history))
        assert signal.action == "buy"
        assert "atr_breakout" in signal.reason

    def test_hold_when_no_breakout(self):
        s = LiveBreakoutWithAtrStop()
        n = 40
        closes = np.linspace(100, 110, n).tolist()
        closes[-1] = 105.0  # below recent max
        history = _ohlcv(np.array(closes))
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert "no_breakout" in signal.reason

    def test_hold_when_warmup(self):
        s = LiveBreakoutWithAtrStop()
        history = _ohlcv(np.linspace(100, 110, 5))
        signal = _run(s, _ctx(history))
        assert signal.action == "hold"
        assert signal.reason == "warmup"


class TestAtrFloorAndBreakoutBuffer:
    """#326 회귀 박제 — 5/26 BTC 3건 churn (-11 USDT) 차단.

    Root cause 1: `_atr_overrides._to_pct()` 가 cap (0.999) 만 있고 floor 없음 →
    저변동성 시간대 ATR 이 작으면 trailing_stop 이 0.058% 까지 좁아져 진입 직후
    노이즈로 즉시 청산 (BTC 5/26 00:00 KST 3건 1-37초 손절).

    Root cause 2: breakout 조건이 ``last < prior_max`` 비교라 ``last == max``
    동률·미세 돌파 (+0.004%) 가 entry 통과 → 실질 돌파 없는 marginal entry.
    """

    def test_atr_trailing_pct_has_floor_in_low_volatility(self):
        """저변동성: ATR×mult/price < 0.003 일 때 trail pct 가 0.003 floor 적용."""
        s = LiveBreakoutWithAtrStop(trailing_stop_atr_mult=1.5, atr_period=14)
        n = 40
        closes = np.full(n, 100.0)
        # high-low 범위 0.01% of price — ATR ≈ 0.01 → trail = 0.01×1.5/100 = 0.00015
        idx = pd.date_range("2026-01-01", periods=n, freq="1min")
        history = pd.DataFrame({
            "open": closes,
            "high": closes * 1.00005,
            "low": closes * 0.99995,
            "close": closes,
            "volume": np.full(n, 1000.0),
        }, index=idx)
        overrides = s._atr_overrides(history, last_close=float(closes[-1]))
        assert overrides["trail"] is not None
        assert overrides["trail"] >= 0.003, (
            f"trail_pct 0.003 floor 미적용 — 저변동성 시간대 진입 직후 noise "
            f"청산 churn 차단 실패. got={overrides['trail']}"
        )

    def test_atr_stop_pct_has_same_floor(self):
        """stop_loss / take_profit 도 같은 floor 적용 — 일관성."""
        s = LiveBreakoutWithAtrStop(
            stop_atr_mult=1.5,
            take_profit_atr_mult=1.5,
            trailing_stop_atr_mult=1.5,
            atr_period=14,
        )
        n = 40
        closes = np.full(n, 100.0)
        idx = pd.date_range("2026-01-01", periods=n, freq="1min")
        history = pd.DataFrame({
            "open": closes,
            "high": closes * 1.00005,
            "low": closes * 0.99995,
            "close": closes,
            "volume": np.full(n, 1000.0),
        }, index=idx)
        overrides = s._atr_overrides(history, last_close=float(closes[-1]))
        for k in ("stop", "tp", "trail"):
            assert overrides[k] >= 0.003, (
                f"{k} pct 0.003 floor 미적용. got={overrides[k]}"
            )

    def test_atr_trailing_pct_unchanged_in_normal_volatility(self):
        """평소 변동성 (ATR 정상) 에선 ATR 계산값 그대로 — 회귀 X."""
        s = LiveBreakoutWithAtrStop(trailing_stop_atr_mult=1.5, atr_period=14)
        n = 40
        closes = np.linspace(100, 110, n)
        idx = pd.date_range("2026-01-01", periods=n, freq="1min")
        # 정상 변동성 — high/low ±2%
        history = pd.DataFrame({
            "open": closes,
            "high": closes * 1.02,
            "low": closes * 0.98,
            "close": closes,
            "volume": np.full(n, 1000.0),
        }, index=idx)
        overrides = s._atr_overrides(history, last_close=float(closes[-1]))
        # 정상 ATR 이면 0.003 보다 충분히 크고 0.999 cap 아래 — floor 영향 X
        assert 0.003 < overrides["trail"] < 0.999

    def test_hold_when_last_equals_prior_max(self):
        """breakout 동률 (last == max20) → hold. 5/26 trade 1 사례 차단."""
        s = LiveBreakoutWithAtrStop()
        # baseline (20봉) 모두 110, last 도 110 → 동률
        closes = np.concatenate([np.full(20, 105.0), np.full(20, 110.0)]).tolist()
        # closes[-1] = 110.0 (이미 그렇지만 명시)
        closes[-1] = 110.0
        history = _ohlcv(np.array(closes))
        signal = _run(s, _ctx(history))
        assert signal.action == "hold", (
            f"동률 breakout (last==max20) 차단 실패: action={signal.action}, "
            f"reason={signal.reason}"
        )
        assert "no_breakout" in signal.reason

    def test_hold_when_last_within_buffer(self):
        """last 가 prior_max × 1.001 미만 → hold (0.1% 마진 미만)."""
        s = LiveBreakoutWithAtrStop()
        closes = np.concatenate([np.full(20, 105.0), np.full(20, 110.0)]).tolist()
        closes[-1] = 110.05  # +0.045% — 0.1% margin 미달
        history = _ohlcv(np.array(closes))
        signal = _run(s, _ctx(history))
        assert signal.action == "hold", (
            f"미세 돌파 (+0.045%) 차단 실패: action={signal.action}, "
            f"reason={signal.reason}"
        )
        assert "no_breakout" in signal.reason

    def test_buy_when_breakout_exceeds_buffer(self):
        """last >= prior_max × 1.001 → 정상 buy (실질 돌파 통과)."""
        s = LiveBreakoutWithAtrStop()
        closes = np.concatenate([np.full(20, 105.0), np.full(20, 110.0)]).tolist()
        closes[-1] = 110.50  # +0.45% — 0.1% margin 충족
        history = _ohlcv(np.array(closes))
        signal = _run(s, _ctx(history))
        assert signal.action == "buy", (
            f"정상 돌파 (+0.45%) 차단됨 — buffer 너무 빡빡: reason={signal.reason}"
        )
        assert "atr_breakout" in signal.reason
