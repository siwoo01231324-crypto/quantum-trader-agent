"""Tests for src/signals/lookahead_guard.py — append-tail-bar invariance."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _make_ohlcv(n: int = 80, seed: int = 0) -> pd.DataFrame:
    """Deterministic random-walk OHLCV for guard tests."""
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.standard_normal(n) * 0.5)
    high = close + rng.random(n) * 0.5
    low = close - rng.random(n) * 0.5
    volume = rng.integers(1_000, 10_000, size=n).astype(float)
    return pd.DataFrame({"close": close, "high": high, "low": low, "volume": volume})


def test_assert_no_lookahead_pass_on_causal_factor():
    """A causal rolling mean must pass the guard."""
    from signals.lookahead_guard import assert_no_lookahead

    def causal(close: pd.Series, window: int = 5) -> pd.Series:
        return close.rolling(window).mean()

    ohlcv = _make_ohlcv()
    assert_no_lookahead(causal, ohlcv, inputs=["close"], window=5)


def test_assert_no_lookahead_fail_on_lookahead_factor():
    """A factor that looks forward must trip the guard."""
    from signals.lookahead_guard import assert_no_lookahead

    def looks_forward(close: pd.Series) -> pd.Series:
        # Using close.shift(-1) reveals tomorrow's price — classic lookahead.
        return close.shift(-1)

    ohlcv = _make_ohlcv()
    with pytest.raises(AssertionError, match="lookahead"):
        assert_no_lookahead(looks_forward, ohlcv, inputs=["close"])


def test_assert_no_lookahead_handles_dataframe_output():
    """DataFrame-returning factors (MACD, Bollinger) must be checked column-wise."""
    from signals.bollinger import compute_bollinger
    from signals.lookahead_guard import assert_no_lookahead

    ohlcv = _make_ohlcv(n=100)
    assert_no_lookahead(compute_bollinger, ohlcv, inputs=["close"], window=20, n_std=2.0)


def test_assert_no_lookahead_handles_object_dtype():
    """sma_cross returns object-dtype 'signal' column with None/'golden'/'dead'."""
    from signals.lookahead_guard import assert_no_lookahead
    from signals.sma import compute_sma_cross

    ohlcv = _make_ohlcv(n=120, seed=1)
    assert_no_lookahead(
        compute_sma_cross,
        ohlcv,
        inputs=["close"],
        short_window=5,
        long_window=20,
    )


@pytest.mark.parametrize("name", ["rsi", "sma", "sma_cross", "atr", "macd", "bollinger", "realized_vol"])
def test_all_registered_factors_causal(name: str):
    """Every registered factor must be causal over a generic OHLCV sample."""
    import signals  # ensure registry populated
    from signals.lookahead_guard import assert_no_lookahead
    from signals.registry import FACTOR_REGISTRY

    assert name in FACTOR_REGISTRY, f"{name!r} not registered"
    spec = FACTOR_REGISTRY[name]
    ohlcv = _make_ohlcv(n=150, seed=42)
    assert_no_lookahead(spec.func, ohlcv, inputs=spec.inputs, **spec.default_params)
