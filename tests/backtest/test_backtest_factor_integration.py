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


class _FactorProbeAllBarsStrategy:
    """바마다 context['factors'] 를 deep copy 하여 누적 캡처."""

    required_factors: ClassVar[list[str]] = ["rsi", "sma", "atr", "macd", "bollinger"]

    def __init__(self) -> None:
        self.captured: list[dict[str, pd.Series | pd.DataFrame]] = []

    def on_init(self, context: dict) -> None:
        pass

    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
        snap = {name: series.copy() for name, series in context["factors"].items()}
        self.captured.append(snap)
        return Signal(action="hold", size=0.0, reason="probe-all-bars")


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


def test_precompute_bit_identical_all_bars():
    from signals.registry import FACTOR_REGISTRY, compute

    ohlcv = _make_ohlcv(500, seed=42)
    strat = _FactorProbeAllBarsStrategy()
    run_backtest(ohlcv, strat, BacktestConfig(max_drawdown_halt_pct=1.0))

    assert len(strat.captured) == 500, "expected one capture per bar"

    for name in strat.required_factors:
        spec = FACTOR_REGISTRY[name]
        for i in range(500):
            history_prefix = ohlcv.iloc[: i + 1]
            kwargs = {
                col: history_prefix[col]
                for col in spec.inputs
                if col in history_prefix.columns
            }
            expected = compute(name, **kwargs, **spec.default_params)
            got = strat.captured[i][name]
            if isinstance(expected, pd.DataFrame):
                pd.testing.assert_frame_equal(
                    got, expected, check_exact=True, check_names=False
                )
            else:
                pd.testing.assert_series_equal(
                    got, expected, check_exact=True, check_names=False
                )


class _FiveFactorStrategy:
    """5 팩터 동시 요구 — perf 게이트용."""

    required_factors: ClassVar[list[str]] = ["rsi", "sma", "atr", "macd", "bollinger"]

    def on_init(self, context: dict) -> None:
        pass

    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
        return Signal(action="hold", size=0.0, reason="perf-probe")


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

    실측 2026-04-24 dev: 14.96s (선계산 O(N) 경로, gate <60s PASS)
    """
    n = 70_000
    ohlcv = _make_ohlcv(n, seed=99)
    strat = _FactorProbeStrategy()

    start = time.perf_counter()
    run_backtest(ohlcv, strat, BacktestConfig(max_drawdown_halt_pct=1.0))
    elapsed = time.perf_counter() - start

    print(f"wall time (rsi single): {elapsed:.2f}s")
    assert elapsed < 60.0, (
        f"run_backtest on {n} bars took {elapsed:.1f}s (>60s). "
        "Open a follow-up issue for incremental factor computation."
    )


@pytest.mark.slow
def test_5factor_perf():
    """5 팩터 동시 precompute wall time gate — 70k bar < 120s (#81).

    실측 2026-04-24 dev: 14.96s single RSI / 14.34s 5-factor (선계산 O(N) 경로)
    """
    n = 70_000
    ohlcv = _make_ohlcv(n, seed=123)
    strat = _FiveFactorStrategy()

    start = time.perf_counter()
    run_backtest(ohlcv, strat, BacktestConfig(max_drawdown_halt_pct=1.0))
    elapsed = time.perf_counter() - start

    print(f"wall time (5-factor): {elapsed:.2f}s")
    assert elapsed < 120.0, (
        f"run_backtest on {n} bars with 5 factors took {elapsed:.1f}s (>120s). "
        "Precompute path may need numpy view optimization (#81 SF-1)."
    )
