"""Engine ↔ factor registry integration tests + O(N^2) perf gate."""
from __future__ import annotations

import time
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

from backtest.engine import BacktestConfig, run_backtest
from backtest.protocol import Bar, Signal


def _make_ohlcv(n: int, freq: str = "15min", seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    close = 100 + np.cumsum(rng.standard_normal(n) * 0.3)
    high = close + rng.random(n) * 0.2
    low = close - rng.random(n) * 0.2
    open_ = close + rng.standard_normal(n) * 0.1
    volume = rng.integers(1_000, 10_000, size=n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


class _FactorProbeStrategy:
    """Hold-only strategy that captures the last context delivered by engine."""

    required_factors: ClassVar[list[str]] = ["rsi"]

    def __init__(self) -> None:
        self.last_context: dict | None = None
        self.last_history_close: pd.Series | None = None

    def on_init(self, context: dict) -> None:
        pass

    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
        self.last_context = context
        self.last_history_close = history["close"].copy()
        return Signal(action="hold", size=0.0, reason="probe")


class _NoFactorStrategy:
    """Strategy without `required_factors` — must behave exactly like pre-#71."""

    def on_init(self, context: dict) -> None:
        pass

    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
        self._last_context = context
        return Signal(action="hold", size=0.0, reason="probe-no-factor")


def test_engine_injects_required_factors_into_context():
    from signals.rsi import compute_rsi

    ohlcv = _make_ohlcv(80, seed=1)
    strat = _FactorProbeStrategy()
    run_backtest(ohlcv, strat)

    assert strat.last_context is not None
    assert "factors" in strat.last_context
    assert "rsi" in strat.last_context["factors"]

    expected = compute_rsi(strat.last_history_close, 14)
    got = strat.last_context["factors"]["rsi"]
    pd.testing.assert_series_equal(got, expected, check_names=False)


def test_engine_skips_factors_when_empty():
    ohlcv = _make_ohlcv(50, seed=2)
    strat = _NoFactorStrategy()
    run_backtest(ohlcv, strat)

    assert "factors" not in strat._last_context, (
        "context['factors'] must not appear when required_factors is empty/missing"
    )


def test_engine_factor_length_matches_history():
    ohlcv = _make_ohlcv(60, seed=3)
    strat = _FactorProbeStrategy()
    run_backtest(ohlcv, strat)

    factor = strat.last_context["factors"]["rsi"]
    # On the final bar the history is the full ohlcv
    assert len(factor) == len(ohlcv)


def test_engine_rejects_unregistered_factor():
    class _BogusStrat:
        required_factors: ClassVar[list[str]] = ["never_registered"]

        def on_init(self, context: dict) -> None:
            pass

        def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
            return Signal(action="hold", size=0.0, reason="x")

    ohlcv = _make_ohlcv(20)
    with pytest.raises(KeyError, match="never_registered"):
        run_backtest(ohlcv, _BogusStrat())


@pytest.mark.slow
def test_rsi_perf():
    """Issue #71 benchmark gate — 70k-bar BTCUSDT-like 15m run.

    **Gate failed** at implementation time (2026-04-23): see
    docs/work/active/000071-alpha-factors/02_perf_benchmark.md for empirical
    scaling. Root cause: rsi.compute_rsi contains a Python-level for-loop, and
    the engine recomputes each factor over the full history every bar
    (O(N^2) calls x O(N) per-call = effectively O(N^2) wall time dominated by
    Python overhead). A follow-up issue must be opened for incremental /
    vectorized factor computation before merging #71 into production use.

    Marked `slow` so default CI skips it. Run locally with:
        pytest -m slow tests/test_backtest_factor_integration.py::test_rsi_perf
    """
    n = 70_000
    ohlcv = _make_ohlcv(n, seed=99)
    strat = _FactorProbeStrategy()

    start = time.perf_counter()
    run_backtest(ohlcv, strat, BacktestConfig(max_drawdown_halt_pct=1.0))
    elapsed = time.perf_counter() - start

    assert elapsed < 60.0, (
        f"run_backtest on {n} bars took {elapsed:.1f}s (>60s). "
        "Open a follow-up issue for incremental factor computation."
    )
