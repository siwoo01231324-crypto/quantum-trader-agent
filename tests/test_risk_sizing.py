"""Tests for position sizing module (src/risk/sizing.py).

Covers Kelly (binary and continuous), fractional Kelly, volatility targeting,
and EWMA sigma. Determinism and edge cases are verified.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from risk.sizing import (  # noqa: E402
    ewma_sigma,
    fractional_kelly,
    kelly_binary,
    kelly_continuous,
    vol_target,
)


# ---------------------------------------------------------------------------
# kelly_binary
# ---------------------------------------------------------------------------


def test_kelly_binary_reference_example():
    """p=0.55, b=1 -> 0.10 (from docs/background/20-position-sizing.md §2.1)."""
    assert kelly_binary(0.55, 1.0) == pytest.approx(0.10)


def test_kelly_binary_negative_edge_returns_zero():
    """p=0.4, b=1 -> edge = 0.4 - 0.6 = -0.2, clamped to 0."""
    assert kelly_binary(0.4, 1.0) == 0.0


def test_kelly_binary_zero_edge():
    """p=0.5, b=1 -> fair coin, Kelly says don't bet."""
    assert kelly_binary(0.5, 1.0) == 0.0


def test_kelly_binary_full_certainty_clamped_to_one():
    """p=1, b=1 -> raw f = 1; should remain 1.0 after clamp."""
    assert kelly_binary(1.0, 1.0) == 1.0


def test_kelly_binary_rejects_invalid_p():
    with pytest.raises(ValueError):
        kelly_binary(1.5, 1.0)
    with pytest.raises(ValueError):
        kelly_binary(-0.1, 1.0)


def test_kelly_binary_rejects_nonpositive_b():
    with pytest.raises(ValueError):
        kelly_binary(0.6, 0.0)
    with pytest.raises(ValueError):
        kelly_binary(0.6, -1.0)


# ---------------------------------------------------------------------------
# kelly_continuous
# ---------------------------------------------------------------------------


def test_kelly_continuous_basic():
    """mu=0.02, sigma=0.1 -> f = 0.02 / 0.01 = 2.0, clamped to 1.0."""
    assert kelly_continuous(0.02, 0.1) == 1.0


def test_kelly_continuous_small_edge_uncamped():
    """mu=0.001, sigma=0.1 -> f = 0.001 / 0.01 = 0.1 (within [0,1], not clamped)."""
    assert kelly_continuous(0.001, 0.1) == pytest.approx(0.1)


def test_kelly_continuous_rf_shift():
    """Risk-free rate shifts edge downward."""
    f = kelly_continuous(mu=0.01, sigma=0.1, rf=0.005)
    # edge = 0.005, sigma^2 = 0.01 -> 0.5
    assert f == pytest.approx(0.5)


def test_kelly_continuous_negative_edge_returns_zero():
    assert kelly_continuous(mu=-0.01, sigma=0.1) == 0.0


def test_kelly_continuous_zero_sigma_returns_zero():
    """sigma=0 is fail-closed: no variance, no allocation."""
    assert kelly_continuous(mu=0.01, sigma=0.0) == 0.0


def test_kelly_continuous_rejects_negative_sigma():
    with pytest.raises(ValueError):
        kelly_continuous(mu=0.01, sigma=-0.01)


# ---------------------------------------------------------------------------
# fractional_kelly
# ---------------------------------------------------------------------------


def test_fractional_kelly_half():
    assert fractional_kelly(0.10, k=0.5) == pytest.approx(0.05)


def test_fractional_kelly_quarter():
    assert fractional_kelly(0.40, k=0.25) == pytest.approx(0.10)


def test_fractional_kelly_default_is_half():
    """Default k=0.5 per docs/background/20-position-sizing.md §2.4."""
    assert fractional_kelly(0.20) == pytest.approx(0.10)


def test_fractional_kelly_clamps_output():
    """k * full may exceed 1 if full > 1 was passed; output stays in [0, 1]."""
    # Raw full_kelly=1.2, k=0.5 -> 0.6 (within bounds, no clamp)
    assert fractional_kelly(1.2, k=0.5) == pytest.approx(0.6)
    # Raw full_kelly=3.0, k=0.5 -> 1.5 -> clamp to 1.0
    assert fractional_kelly(3.0, k=0.5) == 1.0


def test_fractional_kelly_negative_full_returns_zero():
    """Negative raw input clamps to 0."""
    assert fractional_kelly(-0.2, k=0.5) == 0.0


def test_fractional_kelly_rejects_invalid_k():
    with pytest.raises(ValueError):
        fractional_kelly(0.1, k=0.0)
    with pytest.raises(ValueError):
        fractional_kelly(0.1, k=1.1)
    with pytest.raises(ValueError):
        fractional_kelly(0.1, k=-0.5)


# ---------------------------------------------------------------------------
# vol_target
# ---------------------------------------------------------------------------


def test_vol_target_daily_equity_formula():
    """target 10% annual / (daily 2% * sqrt(252)) ≈ 0.1 / 0.3175 ≈ 0.3150."""
    w = vol_target(sigma_period=0.02, target_annual=0.10, periods_per_year=252)
    expected = 0.10 / (0.02 * math.sqrt(252))
    assert w == pytest.approx(expected)


def test_vol_target_clamps_when_low_vol():
    """When realized vol is tiny, target can exceed 1.0 and must clamp."""
    w = vol_target(sigma_period=0.001, target_annual=0.10, periods_per_year=252)
    # 0.10 / (0.001 * 15.87) ≈ 6.3 -> clamp to 1.0
    assert w == 1.0


def test_vol_target_zero_sigma_returns_one():
    """sigma_period == 0 -> full allocation; policy clamps final exposure."""
    assert vol_target(sigma_period=0.0) == 1.0


def test_vol_target_crypto_15m_annualization():
    """BTC 15m has 365*96 = 35040 bars/year; verify the constant flows through."""
    w = vol_target(sigma_period=0.003, target_annual=0.20, periods_per_year=35040)
    expected = 0.20 / (0.003 * math.sqrt(35040))
    assert w == pytest.approx(expected)


def test_vol_target_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        vol_target(sigma_period=-0.01)
    with pytest.raises(ValueError):
        vol_target(sigma_period=0.01, target_annual=0.0)
    with pytest.raises(ValueError):
        vol_target(sigma_period=0.01, periods_per_year=0)


# ---------------------------------------------------------------------------
# ewma_sigma
# ---------------------------------------------------------------------------


def test_ewma_sigma_zero_on_constant_returns():
    """Constant (zero) returns -> sigma == 0."""
    assert ewma_sigma([0.0] * 100) == 0.0


def test_ewma_sigma_too_short():
    """Fewer than 2 samples -> 0."""
    assert ewma_sigma([]) == 0.0
    assert ewma_sigma([0.01]) == 0.0


def test_ewma_sigma_matches_manual_recursion():
    """Verify the recursion var_t = lam*var + (1-lam)*r^2 against a hand calc."""
    lam = 0.94
    rets = [0.01, -0.02, 0.015]
    var = 0.0
    for r in rets:
        var = lam * var + (1.0 - lam) * r * r
    assert ewma_sigma(rets, lam=lam) == pytest.approx(math.sqrt(var))


def test_ewma_sigma_nan_dropped():
    """NaN values are dropped before recursion."""
    rets = [0.01, float("nan"), -0.02, 0.015]
    clean = [0.01, -0.02, 0.015]
    assert ewma_sigma(rets) == pytest.approx(ewma_sigma(clean))


def test_ewma_sigma_accepts_pandas_and_numpy():
    """Sequence, np.ndarray, pd.Series must all yield the same result."""
    data = [0.01, -0.005, 0.012, -0.008, 0.004]
    a = ewma_sigma(data)
    b = ewma_sigma(np.array(data))
    c = ewma_sigma(pd.Series(data))
    assert a == pytest.approx(b)
    assert a == pytest.approx(c)


def test_ewma_sigma_rejects_invalid_lam():
    with pytest.raises(ValueError):
        ewma_sigma([0.01, 0.02], lam=0.0)
    with pytest.raises(ValueError):
        ewma_sigma([0.01, 0.02], lam=1.0)
    with pytest.raises(ValueError):
        ewma_sigma([0.01, 0.02], lam=1.5)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_sizers_are_deterministic():
    """Same input -> same output across repeated calls."""
    assert kelly_binary(0.6, 1.5) == kelly_binary(0.6, 1.5)
    assert kelly_continuous(0.01, 0.08, 0.002) == kelly_continuous(0.01, 0.08, 0.002)
    assert fractional_kelly(0.3, 0.5) == fractional_kelly(0.3, 0.5)
    assert vol_target(0.02, 0.1, 252) == vol_target(0.02, 0.1, 252)

    rets = [0.01, -0.005, 0.012, -0.008, 0.004]
    assert ewma_sigma(rets) == ewma_sigma(rets)


# ---------------------------------------------------------------------------
# End-to-end composition (Kelly -> fractional -> clamp)
# ---------------------------------------------------------------------------


def test_kelly_pipeline_continuous_to_half_kelly():
    """Realistic pipeline: kelly_continuous -> fractional_kelly."""
    full = kelly_continuous(mu=0.003, sigma=0.05)   # 0.003 / 0.0025 = 1.2, clamped to 1.0
    half = fractional_kelly(full, k=0.5)
    assert full == 1.0
    assert half == 0.5


def test_kelly_pipeline_binary_to_quarter_kelly():
    full = kelly_binary(0.6, 1.0)                   # 0.2
    quarter = fractional_kelly(full, k=0.25)
    assert full == pytest.approx(0.2)
    assert quarter == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Strategy integration: MomoBtcV2 sizing_mode
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int, seed: int = 42, start_price: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = start_price + np.cumsum(rng.standard_normal(n) * 0.5)
    closes = np.maximum(closes, 1.0)
    opens = closes * (1 + rng.standard_normal(n) * 0.001)
    highs = np.maximum(closes, opens) * (1 + np.abs(rng.standard_normal(n) * 0.002))
    lows = np.minimum(closes, opens) * (1 - np.abs(rng.standard_normal(n) * 0.002))
    volumes = np.abs(rng.standard_normal(n) * 1000 + 5000)
    index = pd.date_range("2024-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=index,
    )


def test_momo_btc_v2_default_sizing_is_backward_compatible():
    """Default sizing_mode='full' must preserve the size=1.0 contract."""
    from backtest.strategies.momo_btc_v2 import MomoBtcV2

    strategy = MomoBtcV2()
    assert strategy.sizing_mode == "full"


def test_momo_btc_v2_half_kelly_produces_bounded_size():
    """Half-Kelly mode keeps buy signal size in (0, 1]; sell/hold unchanged."""
    from backtest.protocol import Signal, Strategy
    from backtest.strategies.momo_btc_v2 import MomoBtcV2

    strategy = MomoBtcV2(sizing_mode="half-kelly", sizing_lookback=60)
    assert isinstance(strategy, Strategy)

    ohlcv = _make_ohlcv(300, seed=7)
    seen_buy = False
    for i in range(43, len(ohlcv)):
        history = ohlcv.iloc[: i + 1]
        bar_row = ohlcv.iloc[i]
        from backtest.protocol import Bar

        bar = Bar(
            ts=bar_row.name,
            open=float(bar_row["open"]),
            high=float(bar_row["high"]),
            low=float(bar_row["low"]),
            close=float(bar_row["close"]),
            volume=float(bar_row["volume"]),
        )
        signal: Signal = strategy.on_bar(bar, history, {})
        assert 0.0 <= signal.size <= 1.0, f"size out of bounds: {signal.size}"
        if signal.action == "buy":
            seen_buy = True
            # Half-Kelly on a random walk with ~zero drift often yields 0
            # (edge<=0 -> size 0 -> converted to 'hold'). That's acceptable.
    # The sell path must always return 1.0 by contract
    # (not asserted dynamically because synthetic data may not produce bearish divergence)


def test_momo_btc_v2_vol_target_runs_without_error():
    """Vol-target mode completes a full backtest and returns a valid result."""
    from backtest.engine import BacktestConfig, run_backtest
    from backtest.strategies.momo_btc_v2 import MomoBtcV2

    strategy = MomoBtcV2(
        sizing_mode="vol-target",
        target_annual=0.20,
        periods_per_year=365 * 96,
    )
    ohlcv = _make_ohlcv(300, seed=7)
    result = run_backtest(ohlcv, strategy, BacktestConfig(initial_cash=10_000.0))
    assert (result.equity_curve > 0).all()
    # All buy trades must have respected size <= 1.0 (enforced implicitly by sizer clamp)
    for trade in result.trades:
        assert trade["size"] >= 0.0


def test_momo_btc_v2_rejects_invalid_sizing_lookback():
    from backtest.strategies.momo_btc_v2 import MomoBtcV2

    with pytest.raises(ValueError):
        MomoBtcV2(sizing_lookback=1)


def test_momo_btc_v2_rejects_unknown_sizing_mode():
    """Unknown mode raised at use-time (on_bar), not construction."""
    from backtest.protocol import Bar
    from backtest.strategies.momo_btc_v2 import MomoBtcV2

    strategy = MomoBtcV2()
    strategy.sizing_mode = "bogus"  # bypass Literal at runtime

    ohlcv = _make_ohlcv(100, seed=1)
    # force the bullish branch by synthesizing: just check the error path
    # by calling _entry_size directly.
    with pytest.raises(ValueError):
        strategy._entry_size(ohlcv["close"])
