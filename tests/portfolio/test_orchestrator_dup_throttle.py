"""#238 — orchestrator-level duplicate-order throttle (defense-in-depth).

Item 1 throttles momo at the strategy layer. This is the orchestrator-level
backstop for ANY non-live-scanner strategy (momo_kis_v1, future single-ticker)
that floods identical (sid, symbol, side) intents every WS tick.

DESIGN: opt-in wall-clock window, DEFAULT 0.0 = DISABLED so every existing
test, backtest, and universe-scan rebalance stays bit-identical. Live config
opts in. Live-scanner strategies are excluded — they keep their own
``_live_entered`` lifecycle.
"""
from __future__ import annotations

import asyncio
from typing import ClassVar

import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from portfolio import AsyncStrategyOrchestrator
import portfolio._async_orchestrator as orch_mod
from risk.dsl import Policy

# #238 — BTCUSDT must size against USDT equity (not KRW). Before #238 the
# orchestrator emitted the raw resolve_size fraction directly as the coin qty
# (the -2019 Margin-insufficient bug); these dup-throttle tests only need a
# non-dropped intent, so equity_usdt is supplied so size_to_qty yields a real
# qty. (Was implicitly relying on the pre-#238 raw-fraction behaviour.)
SNAP = {
    "symbol": "BTCUSDT", "price": 50_000.0,
    "equity_krw": 1_000_000.0, "equity_usdt": 1_000_000.0,
}


def _orch(**kw) -> AsyncStrategyOrchestrator:
    return AsyncStrategyOrchestrator(Policy(policy_version=1, name="test"), **kw)


class _AlwaysBuy:
    is_live_scanner: ClassVar[bool] = False

    def on_bar(self, ctx) -> Signal:
        return Signal(action="buy", size=0.1, reason="always_buy")


class _Switch:
    """Emits a caller-controlled action so opposite-side keys can be tested."""

    is_live_scanner: ClassVar[bool] = False

    def __init__(self) -> None:
        self.action = "buy"

    def on_bar(self, ctx) -> Signal:
        return Signal(action=self.action, size=0.1, reason=f"sig_{self.action}")


class _AlwaysBuyScanner(LiveScannerMixin):
    async def on_bar(self, ctx) -> Signal:
        return Signal(action="buy", size=0.05, reason="scanner_buy")


def _run(orch) -> list:
    return asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), SNAP))


def test_default_disabled_repeated_intents_flow():
    """Default (no min_order_interval_sec) → every repeated buy still routes.

    Guards universe-scan rebalance / all existing orchestrator tests against
    a semantic change. This is the bit-identical contract.
    """
    orch = _orch()
    orch.register_strategy("s", _AlwaysBuy())
    counts = [len(_run(orch)) for _ in range(4)]
    assert counts == [1, 1, 1, 1], counts


def test_interval_suppresses_consecutive_identical():
    orch = _orch(min_order_interval_sec=60.0)
    orch.register_strategy("s", _AlwaysBuy())
    first = _run(orch)
    second = _run(orch)
    third = _run(orch)
    assert len(first) == 1
    assert second == []
    assert third == []


def test_opposite_side_within_window_not_suppressed():
    """A different action (reversal/exit) must never be throttled."""
    orch = _orch(min_order_interval_sec=60.0)
    sw = _Switch()
    orch.register_strategy("s", sw)
    sw.action = "buy"
    assert len(_run(orch)) == 1          # buy emitted
    assert _run(orch) == []              # duplicate buy suppressed
    sw.action = "sell"
    assert len(_run(orch)) == 1          # sell (different key) flows
    assert _run(orch) == []              # duplicate sell suppressed


def test_window_elapsed_allows_again(monkeypatch):
    fake = {"t": 1000.0}
    monkeypatch.setattr(orch_mod.time, "monotonic", lambda: fake["t"])
    orch = _orch(min_order_interval_sec=60.0)
    orch.register_strategy("s", _AlwaysBuy())
    assert len(_run(orch)) == 1
    fake["t"] += 30.0
    assert _run(orch) == []
    fake["t"] += 31.0  # > 60s since first
    assert len(_run(orch)) == 1, "must resume after window elapses"


def test_live_scanner_excluded_from_dup_throttle():
    """Live-scanner stays governed by _live_entered, not this throttle."""
    orch = _orch(min_order_interval_sec=60.0)
    orch.register_strategy("scanner", _AlwaysBuyScanner())
    snap = {"equity_krw": 1_000_000.0, "equity_usdt": 1_000_000.0,
            "ohlcv_history": {"BTCUSDT": _ohlcv()}}
    first = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snap))
    second = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-02"), snap))
    assert len(first) == 1                       # first entry allowed
    assert second == []                          # blocked by _live_entered
    orch.release_live_position("scanner", "BTCUSDT")
    third = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-03"), snap))
    assert len(third) == 1, "re-entry after release (live-scanner lifecycle)"


def _ohlcv(n: int = 30) -> pd.DataFrame:
    import numpy as np
    close = 100 + np.cumsum(np.random.default_rng(1).normal(0, 0.5, n))
    close = np.maximum(close, 1.0)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": close, "high": close * 1.001, "low": close * 0.999,
         "close": close, "volume": np.full(n, 1000.0)}, index=idx,
    )
