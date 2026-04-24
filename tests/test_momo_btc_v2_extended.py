"""Extended tests for MomoBtcV2 — _compute_confidence + Signal emission (issue #76)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _synthetic_ohlcv(n: int = 300, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 30_000.0 + np.cumsum(rng.standard_normal(n) * 50.0)
    closes = np.maximum(closes, 100.0)
    opens = closes * (1 + rng.standard_normal(n) * 0.001)
    highs = np.maximum(closes, opens) * (1 + np.abs(rng.standard_normal(n) * 0.002))
    lows = np.minimum(closes, opens) * (1 - np.abs(rng.standard_normal(n) * 0.002))
    volumes = np.abs(rng.standard_normal(n) * 1000 + 5000)
    index = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=index,
    )


# ---------------------------------------------------------------------------
# _compute_confidence rule tests
# ---------------------------------------------------------------------------

def test_compute_confidence_clip_to_unit():
    """_compute_confidence result must always be in [0, 1]."""
    from backtest.strategies.momo_btc_v2 import MomoBtcV2

    strat = MomoBtcV2()
    # Very large magnitude → clips to 1.0
    assert strat._compute_confidence(1000.0, 1.0, 14) == 1.0
    # Negative magnitude → clips to 0.0 (abs applied)
    result = strat._compute_confidence(-5.0, 2.0, 7)
    assert 0.0 <= result <= 1.0
    # Zero ATR → 0.0
    assert strat._compute_confidence(5.0, 0.0, 7) == 0.0


def test_compute_confidence_formula_known_value():
    """Known-value test: |div|/atr * min(bars/LOOKBACK, 1) = 1.5/2.0 * (1/14) ≈ 0.0536."""
    from backtest.strategies.momo_btc_v2 import MomoBtcV2

    strat = MomoBtcV2()
    result = strat._compute_confidence(div_magnitude=1.5, atr=2.0, bars_since_pivot=1)
    expected = min(1.0, abs(1.5) / 2.0 * min(1 / 14, 1.0))
    assert result == pytest.approx(expected, abs=1e-6)


def test_compute_confidence_full_lookback():
    """bars_since_pivot >= LOOKBACK caps min() at 1.0."""
    from backtest.strategies.momo_btc_v2 import MomoBtcV2

    strat = MomoBtcV2()
    result_14 = strat._compute_confidence(2.0, 2.0, 14)
    result_20 = strat._compute_confidence(2.0, 2.0, 20)
    assert result_14 == pytest.approx(result_20, abs=1e-9)
    assert result_14 == pytest.approx(1.0, abs=1e-9)  # 2/2 * 1 = 1.0


# ---------------------------------------------------------------------------
# Signal emission check: buy signal carries expected_return and confidence
# ---------------------------------------------------------------------------

def test_buy_signal_carries_expected_return_and_confidence():
    """When a buy signal is emitted, it should have expected_return and confidence set."""
    from backtest.strategies.momo_btc_v2 import MomoBtcV2
    from backtest.protocol import Bar, Signal
    from backtest.engine import BacktestConfig, run_backtest

    ohlcv = _synthetic_ohlcv(n=300, seed=7)
    strategy = MomoBtcV2(sizing_mode="full")

    buy_signals = []

    # Monkey-patch on_bar to capture signals
    original_on_bar = strategy.on_bar

    def patched_on_bar(bar, history, context):
        sig = original_on_bar(bar, history, context)
        if sig.action == "buy":
            buy_signals.append(sig)
        return sig

    strategy.on_bar = patched_on_bar

    config = BacktestConfig(initial_cash=10_000.0)
    run_backtest(ohlcv, strategy, config)

    if buy_signals:
        for sig in buy_signals:
            assert sig.action == "buy"
            assert sig.expected_return is not None, "buy signal must have expected_return"
            assert sig.confidence is not None, "buy signal must have confidence"
            assert 0.0 <= sig.confidence <= 1.0


def test_hold_and_sell_signals_optional_fields_can_be_none():
    """hold/sell signals are allowed to have None optional fields."""
    from backtest.protocol import Signal

    hold = Signal(action="hold", size=0.0, reason="warmup")
    sell = Signal(action="sell", size=1.0, reason="bearish divergence")
    assert hold.confidence is None
    assert sell.confidence is None
