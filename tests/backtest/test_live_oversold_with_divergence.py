"""Unit tests for LiveOversoldWithDivergence (#227 S4)."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_oversold_with_divergence import (
    LiveOversoldWithDivergence,
)
from signals.rsi import compute_rsi, detect_divergence


def _bullish_divergence_path(n: int = 80) -> pd.DataFrame:
    """Construct a price path engineered for a bullish RSI divergence on the
    LAST bar — same shape used in scripts/live_run.py::_build_mock_ticks but
    extended to *n* bars.

    Phases (mirrors the production smoke generator):
      0..29  warmup ramp 80,000 → 80,700 (RSI primes near 65)
      30..44 sharp drop 80,700 → 71,000 (RSI ~ 18, first lower low)
      45..59 recovery 71,000 → 78,000 (RSI back ~ 55)
      60..74 milder drop 78,000 → 69,000 — NEW lower low,
              RSI ≈ 30 (HIGHER than first leg) → bullish at bar 75
    """
    def _phase(i: int) -> float:
        if i < 30:
            return 80_000.0 + 24.0 * i
        if i < 45:
            return 80_700.0 - (9_700.0 / 14.0) * (i - 29)
        if i < 60:
            return 71_000.0 + (7_000.0 / 14.0) * (i - 44)
        if i < 75:
            return 78_000.0 - (9_000.0 / 14.0) * (i - 59)
        return 69_000.0 + 250.0 * (i - 74)

    closes = np.array([_phase(i) for i in range(n)])
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {
            "open": closes, "high": closes * 1.001, "low": closes * 0.999,
            "close": closes, "volume": np.full(n, 1_000.0),
        },
        index=idx,
    )


def _ctx_with_rsi(history: pd.DataFrame) -> dict:
    rsi = compute_rsi(history["close"], period=14)
    return {
        "ts": history.index[-1],
        "market_snapshot": {
            "symbol": "005930",
            "history": history,
            "price": float(history["close"].iloc[-1]),
        },
        "factors": {"rsi": rsi},
    }


def _run(strategy, ctx) -> Signal | None:
    return asyncio.run(strategy.on_bar(ctx))


class TestLiveOversoldWithDivergence:
    def test_marker_inheritance(self):
        s = LiveOversoldWithDivergence()
        assert isinstance(s, LiveScannerMixin)

    def test_buy_on_bullish_divergence_in_downtrend(self):
        """Use the live_run mock-tick price path; bar 73 is the boundary
        where ``detect_divergence`` returns 'bullish' (verified empirically).
        """
        s = LiveOversoldWithDivergence()
        history = _bullish_divergence_path(n=120).iloc[: 74]  # bars 0..73
        # Sanity: confirm both gates fire at the chosen boundary.
        c_now = float(history["close"].iloc[-1])
        c_past = float(history["close"].iloc[-22])
        assert c_now < c_past, f"expected downtrend, got now={c_now} past={c_past}"
        rsi = compute_rsi(history["close"], period=14)
        div = detect_divergence(history["close"], rsi, 14)
        assert div.iloc[-1] == "bullish", f"expected bullish div, got {div.iloc[-1]}"

        signal = _run(s, _ctx_with_rsi(history))
        assert signal.action == "buy"
        assert "oversold_divergence" in signal.reason

    def test_hold_when_not_in_downtrend(self):
        s = LiveOversoldWithDivergence()
        n = 80
        # Pure uptrend — no downtrend.
        closes = np.linspace(100, 200, n)
        idx = pd.date_range("2026-01-01", periods=n, freq="15min")
        history = pd.DataFrame(
            {
                "open": closes, "high": closes * 1.001, "low": closes * 0.999,
                "close": closes, "volume": np.full(n, 1_000.0),
            },
            index=idx,
        )
        signal = _run(s, _ctx_with_rsi(history))
        assert signal.action == "hold"
        assert "not_downtrending" in signal.reason

    def test_hold_when_no_divergence(self):
        """Steady decline with RSI co-trending — no bullish divergence."""
        s = LiveOversoldWithDivergence()
        n = 80
        closes = np.linspace(200, 100, n)  # smooth decline
        idx = pd.date_range("2026-01-01", periods=n, freq="15min")
        history = pd.DataFrame(
            {
                "open": closes, "high": closes * 1.001, "low": closes * 0.999,
                "close": closes, "volume": np.full(n, 1_000.0),
            },
            index=idx,
        )
        signal = _run(s, _ctx_with_rsi(history))
        assert signal.action == "hold"
        assert ("no_bullish_divergence" in signal.reason) or ("warmup" in signal.reason)

    def test_hold_when_warmup(self):
        s = LiveOversoldWithDivergence()
        history = _bullish_divergence_path().iloc[:20]
        signal = _run(s, _ctx_with_rsi(history))
        assert signal.action == "hold"
        assert signal.reason == "warmup"

    def test_hold_when_rsi_missing(self):
        s = LiveOversoldWithDivergence()
        history = _bullish_divergence_path()
        ctx = {
            "ts": history.index[-1],
            "market_snapshot": {
                "symbol": "005930",
                "history": history,
                "price": float(history["close"].iloc[-1]),
            },
            "factors": {},
        }
        signal = _run(s, ctx)
        assert signal.action == "hold"
        assert signal.reason == "rsi_missing"
