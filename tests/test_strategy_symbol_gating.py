"""Regression tests for cross-asset symbol gating (#177).

Background: `MomoBtcV2` is calibrated for BTC perp 15m bars. Before #177 it
had no symbol filter, so when the live orchestrator delivered a KRX 005930
snapshot the strategy would still run RSI divergence on the close series and
return a buy signal. That is wrong both economically (wrong-asset sizing) and
operationally (the live loop would route a 005930 buy to the active broker).

These tests pin the gating contract:
  - Strategy returns "symbol_mismatch" hold when ctx symbol != self.symbol
  - Strategy proceeds normally when symbols match
  - Missing ctx symbol stays opt-in (legacy unit tests + engine).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Bar
from backtest.strategies.momo_btc_v2 import MomoBtcV2
from backtest.strategies.momo_kis_v1 import MomoKisV1


def _history(n: int = 60, base: float = 80_000.0) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    closes = base + np.cumsum(rng.normal(0, 50, n))
    idx = pd.date_range("2026-05-04 00:00", periods=n, freq="1min", tz="UTC")
    return pd.DataFrame({
        "open": closes, "high": closes + 50, "low": closes - 50,
        "close": closes, "volume": [1000.0] * n,
    }, index=idx)


# ---------------------------------------------------------------------------
# MomoBtcV2 (sync)
# ---------------------------------------------------------------------------

def test_momo_btc_v2_holds_on_wrong_symbol():
    """KRX 005930 snapshot reaching the BTC strategy must short-circuit."""
    strategy = MomoBtcV2()
    history = _history(60)
    bar = Bar(
        ts=history.index[-1],
        open=float(history["open"].iloc[-1]),
        high=float(history["high"].iloc[-1]),
        low=float(history["low"].iloc[-1]),
        close=float(history["close"].iloc[-1]),
        volume=float(history["volume"].iloc[-1]),
    )
    context = {"ts": bar.ts, "factors": {"rsi": pd.Series(dtype=float)}, "symbol": "005930"}

    sig = strategy.on_bar(bar, history, context)

    assert sig.action == "hold"
    assert sig.reason == "symbol_mismatch"


def test_momo_btc_v2_runs_on_btc_symbol():
    """Matching symbol must NOT short-circuit; strategy reaches its own gates."""
    strategy = MomoBtcV2()
    history = _history(60)
    bar = Bar(
        ts=history.index[-1], open=80_000.0, high=80_100.0, low=79_900.0,
        close=80_050.0, volume=1000.0,
    )
    context = {"ts": bar.ts, "factors": {"rsi": pd.Series(dtype=float)}, "symbol": "BTCUSDT"}

    sig = strategy.on_bar(bar, history, context)

    assert sig.reason != "symbol_mismatch", (
        f"BTC symbol must not be rejected by the symbol gate, got {sig!r}"
    )


def test_momo_btc_v2_missing_symbol_is_opt_in():
    """When context omits 'symbol' (engine / legacy tests), gate stays inactive."""
    strategy = MomoBtcV2()
    history = _history(60)
    bar = Bar(ts=history.index[-1], open=80_000.0, high=80_100.0,
              low=79_900.0, close=80_050.0, volume=1000.0)
    context = {"ts": bar.ts, "factors": {"rsi": pd.Series(dtype=float)}}

    sig = strategy.on_bar(bar, history, context)

    assert sig.reason != "symbol_mismatch"


def test_momo_btc_v2_symbol_kwarg_overrides_default():
    """`production.yaml` can pin the strategy to ETHUSDT / a different perp."""
    strategy = MomoBtcV2(symbol="ETHUSDT")
    history = _history(60)
    bar = Bar(ts=history.index[-1], open=80_000.0, high=80_100.0,
              low=79_900.0, close=80_050.0, volume=1000.0)

    btc_sig = strategy.on_bar(
        bar, history,
        {"ts": bar.ts, "factors": {"rsi": pd.Series(dtype=float)}, "symbol": "BTCUSDT"},
    )
    eth_sig = strategy.on_bar(
        bar, history,
        {"ts": bar.ts, "factors": {"rsi": pd.Series(dtype=float)}, "symbol": "ETHUSDT"},
    )

    assert btc_sig.reason == "symbol_mismatch"
    assert eth_sig.reason != "symbol_mismatch"


# ---------------------------------------------------------------------------
# MomoKisV1 (async)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_momo_kis_v1_holds_on_wrong_symbol():
    """BTCUSDT snapshot reaching the 005930 strategy must short-circuit."""
    strategy = MomoKisV1(symbol="005930")
    # KST 10:15 → UTC 01:15 — a valid 15-minute bar boundary.
    ts = pd.Timestamp("2026-05-04 01:15:00", tz="UTC")
    snapshot = {
        "ts": ts.isoformat(),
        "symbol": "BTCUSDT",
        "price": 80_000.0,
        "history": _history(60),
    }
    sig = await strategy.on_bar({"ts": ts, "market_snapshot": snapshot, "factors": {}})
    assert sig is not None
    assert sig.action == "hold"
    assert sig.reason == "symbol_mismatch"


@pytest.mark.asyncio
async def test_momo_kis_v1_runs_on_matching_symbol():
    strategy = MomoKisV1(symbol="005930")
    ts = pd.Timestamp("2026-05-04 01:15:00", tz="UTC")
    snapshot = {
        "ts": ts.isoformat(),
        "symbol": "005930",
        "price": 80_000.0,
        "history": _history(60),
    }
    sig = await strategy.on_bar({"ts": ts, "market_snapshot": snapshot, "factors": {"rsi": pd.Series(dtype=float)}})
    assert sig is None or sig.reason != "symbol_mismatch"
