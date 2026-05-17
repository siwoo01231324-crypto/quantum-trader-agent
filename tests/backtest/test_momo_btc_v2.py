"""Tests for MomoBtcV2 strategy conformance and signal generation."""
import pytest
import pandas as pd
import numpy as np

from backtest.protocol import Bar, Signal, Strategy
from backtest.engine import run_backtest, BacktestConfig, BacktestResult
from backtest.strategies.momo_btc_v2 import MomoBtcV2
from signals.rsi import compute_rsi, detect_divergence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int, start_price: float = 100.0, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    closes = start_price + np.cumsum(np.random.randn(n) * 0.5)
    closes = np.maximum(closes, 1.0)
    opens = closes * (1 + np.random.randn(n) * 0.001)
    highs = np.maximum(closes, opens) * (1 + np.abs(np.random.randn(n) * 0.002))
    lows = np.minimum(closes, opens) * (1 - np.abs(np.random.randn(n) * 0.002))
    volumes = np.abs(np.random.randn(n) * 1000 + 5000)
    index = pd.date_range("2024-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=index,
    )


def _make_bar(close: float = 100.0) -> Bar:
    return Bar(
        ts=pd.Timestamp("2024-01-01"),
        open=close,
        high=close * 1.001,
        low=close * 0.999,
        close=close,
        volume=1000.0,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

def test_strategy_conforms_to_protocol():
    """MomoBtcV2 must satisfy the Strategy runtime-checkable Protocol."""
    strategy = MomoBtcV2()
    assert isinstance(strategy, Strategy), "MomoBtcV2 does not conform to Strategy protocol"


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def test_buy_on_bullish_divergence():
    """on_bar returns Signal(action='buy') when divergence is 'bullish'."""
    strategy = MomoBtcV2()
    strategy.on_init({})

    n = 200
    ohlcv = _make_ohlcv(n)
    close = ohlcv["close"]
    rsi = compute_rsi(close, strategy.RSI_PERIOD)
    div = detect_divergence(close, rsi, strategy.LOOKBACK)

    # Find a bar where divergence is 'bullish'
    bullish_indices = [i for i in range(len(div)) if div.iloc[i] == "bullish"]
    if not bullish_indices:
        pytest.skip("No bullish divergence in synthetic data; increase n or change seed")

    idx = bullish_indices[0]
    history = ohlcv.iloc[: idx + 1]
    bar = _make_bar(history["close"].iloc[-1])
    context = {"factors": {"rsi": rsi.iloc[: idx + 1]}}
    signal = strategy.on_bar(bar, history, context)
    assert signal.action == "buy", f"Expected 'buy', got '{signal.action}'"
    assert signal.size == 1.0


def test_sell_on_bearish_divergence():
    """on_bar returns Signal(action='sell') when divergence is 'bearish'.

    Uses the deterministic seed-scanning fixture so the core SELL path is
    actually exercised (the old single-seed n=200 form was permanently
    skipped — seed 42 has no bearish divergence).
    """
    strategy = MomoBtcV2()
    strategy.on_init({})
    history, bar, context = _bearish_fixture()
    signal = strategy.on_bar(bar, history, context)
    assert signal.action == "sell", f"Expected 'sell', got '{signal.action}'"
    assert signal.size == 1.0


def test_hold_when_no_divergence():
    """on_bar returns Signal(action='hold') when there is no divergence."""
    strategy = MomoBtcV2()
    strategy.on_init({})

    # Create a perfectly trending series (monotonic up) that typically has no divergence
    n = 100
    closes = pd.Series(range(100, 100 + n), dtype=float)
    opens = closes * 0.999
    highs = closes * 1.002
    lows = closes * 0.998
    volumes = pd.Series([1000.0] * n)
    index = pd.date_range("2024-01-01", periods=n, freq="15min")
    ohlcv = pd.DataFrame(
        {"open": opens.values, "high": highs.values, "low": lows.values,
         "close": closes.values, "volume": volumes.values},
        index=index,
    )

    # Use the full history
    history = ohlcv
    bar = _make_bar(float(closes.iloc[-1]))
    rsi = compute_rsi(closes, strategy.RSI_PERIOD)
    context = {"factors": {"rsi": rsi}}
    signal = strategy.on_bar(bar, history, context)

    div = detect_divergence(closes, rsi, strategy.LOOKBACK)
    latest_div = div.iloc[-1]

    if latest_div is None:
        assert signal.action == "hold"
    else:
        # Accept whatever the strategy computed based on actual divergence
        assert signal.action in ("buy", "sell", "hold")


def test_warmup_returns_hold():
    """Strategy returns 'hold' during warmup period (insufficient bars)."""
    strategy = MomoBtcV2()
    strategy.on_init({})

    # min_bars = RSI_PERIOD + LOOKBACK * 2 + 1 = 14 + 28 + 1 = 43
    # Use only 10 bars (definitely in warmup)
    n = 10
    ohlcv = _make_ohlcv(n)
    bar = _make_bar(ohlcv["close"].iloc[-1])
    signal = strategy.on_bar(bar, ohlcv, {})
    assert signal.action == "hold"
    assert signal.reason == "warmup"


def test_strategy_with_engine_produces_results():
    """run_backtest with MomoBtcV2 on synthetic data returns a BacktestResult."""
    strategy = MomoBtcV2()
    ohlcv = _make_ohlcv(300)
    config = BacktestConfig(initial_cash=10_000.0)

    result = run_backtest(ohlcv, strategy, config)

    assert isinstance(result, BacktestResult)
    assert isinstance(result.equity_curve, pd.Series)
    assert len(result.equity_curve) == 300
    assert isinstance(result.trades, list)
    assert isinstance(result.metrics, dict)
    # Equity should be positive throughout
    assert (result.equity_curve > 0).all(), "Equity went to zero or negative"


# ---------------------------------------------------------------------------
# #238 — live signal-flood throttle
#
# In live (Binance aggTrade WS) on_bar is driven by ticks at dozens/sec. While
# bearish divergence persists, momo emits an identical SELL every tick → the
# orchestrator submits each to Binance → -2019 Margin-insufficient flood.
# Same defect class as the smoke fix (dffd2bc) and live-scanner fix (fbd28b0),
# but momo (single-ticker legacy) was outside both. The throttle is a
# wall-clock interval guard, DEFAULT-OFF so backtests stay bit-identical and
# the 5y Sharpe production gate is preserved; live config opts in.
# ---------------------------------------------------------------------------

def _bearish_fixture(n: int = 400):
    """Return (history, bar, context) positioned at the first bearish bar.

    Deterministic: scans seeds in fixed order and returns the first that
    produces a bearish divergence (avoids the single-seed fragility that
    leaves test_sell_on_bearish_divergence permanently skipped).
    """
    for seed in range(200):
        ohlcv = _make_ohlcv(n, seed=seed)
        close = ohlcv["close"]
        rsi = compute_rsi(close, MomoBtcV2.RSI_PERIOD)
        div = detect_divergence(close, rsi, MomoBtcV2.LOOKBACK)
        bearish = [i for i in range(len(div)) if div.iloc[i] == "bearish"]
        if bearish:
            idx = bearish[0]
            history = ohlcv.iloc[: idx + 1]
            bar = _make_bar(history["close"].iloc[-1])
            context = {"factors": {"rsi": rsi.iloc[: idx + 1]}}
            return history, bar, context
    pytest.skip("No bearish divergence across 200 seeds; widen scan")


def test_default_disabled_emits_every_tick_backtest_bit_identical():
    """Default (min_signal_interval_sec=0.0) → NO throttle.

    Backtest path must be untouched (5y Sharpe gate). Rapid identical bearish
    ticks each still emit SELL exactly like before the throttle existed.
    """
    strategy = MomoBtcV2()  # default → throttle disabled
    strategy.on_init({})
    history, bar, context = _bearish_fixture()
    sigs = [strategy.on_bar(bar, history, context) for _ in range(5)]
    assert all(s.action == "sell" for s in sigs), [s.action for s in sigs]
    assert all(s.reason == "bearish divergence" for s in sigs)


def test_interval_throttles_rapid_bearish_ticks():
    """With interval enabled, only the first bearish tick emits; rest hold."""
    strategy = MomoBtcV2(min_signal_interval_sec=60.0)
    strategy.on_init({})
    history, bar, context = _bearish_fixture()
    first = strategy.on_bar(bar, history, context)
    second = strategy.on_bar(bar, history, context)
    third = strategy.on_bar(bar, history, context)
    assert first.action == "sell"
    assert second.action == "hold"
    assert second.reason == "momo_interval_throttle"
    assert third.action == "hold"
    assert third.reason == "momo_interval_throttle"


def test_throttle_window_elapsed_allows_next_signal(monkeypatch):
    """After the interval elapses, the next actionable tick emits again."""
    import backtest.strategies.momo_btc_v2 as mod

    fake = {"t": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: fake["t"])
    strategy = MomoBtcV2(min_signal_interval_sec=60.0)
    strategy.on_init({})
    history, bar, context = _bearish_fixture()
    assert strategy.on_bar(bar, history, context).action == "sell"
    fake["t"] += 30.0  # within window
    assert strategy.on_bar(bar, history, context).action == "hold"
    fake["t"] += 31.0  # now > 60s since first emit
    again = strategy.on_bar(bar, history, context)
    assert again.action == "sell", "signal must resume after interval elapses"


def test_throttle_does_not_consume_on_hold():
    """Warmup/no-signal holds must not start the throttle window."""
    strategy = MomoBtcV2(min_signal_interval_sec=60.0)
    strategy.on_init({})
    # Warmup hold (insufficient bars) — unrelated to throttle.
    warm = strategy.on_bar(_make_bar(100.0), _make_ohlcv(10), {})
    assert warm.action == "hold"
    assert warm.reason == "warmup"
    # First real bearish tick must still emit (hold did not consume window).
    history, bar, context = _bearish_fixture()
    assert strategy.on_bar(bar, history, context).action == "sell"


# ---------------------------------------------------------------------------
# #238 — stop_loss_pct / take_profit_pct / trailing_stop_pct kwargs
#
# momo-btc-v2 is single-ticker (NOT a LiveScannerMixin) so it had no exit
# thresholds — root incident: a naked -1 BTC short with ZERO auto-stop. The
# new kwargs let scripts/live_run.py source a StopTpPolicy from the strategy
# (production.yaml supplies the values). DEFAULT None → no behavior change;
# the 5y backtest / Sharpe gate stays bit-identical (on_bar untouched).
# ---------------------------------------------------------------------------

def test_stop_tp_kwargs_default_none():
    strategy = MomoBtcV2()
    assert strategy.stop_loss_pct is None
    assert strategy.take_profit_pct is None
    assert strategy.trailing_stop_pct is None


def test_stop_tp_kwargs_stored_when_supplied():
    strategy = MomoBtcV2(
        stop_loss_pct=0.03, take_profit_pct=0.06, trailing_stop_pct=0.02,
    )
    assert strategy.stop_loss_pct == 0.03
    assert strategy.take_profit_pct == 0.06
    assert strategy.trailing_stop_pct == 0.02


def test_stop_tp_kwargs_do_not_alter_backtest_bit_identical():
    """Supplying stop/TP kwargs must NOT change on_bar output anywhere —
    the thresholds are consumed by the live risk manager, not the strategy.
    Backtest path must stay bit-identical (Sharpe production gate)."""
    ohlcv = _make_ohlcv(300)
    config = BacktestConfig(initial_cash=10_000.0)

    baseline = run_backtest(ohlcv, MomoBtcV2(), config)
    with_thresholds = run_backtest(
        ohlcv,
        MomoBtcV2(stop_loss_pct=0.03, take_profit_pct=0.06, trailing_stop_pct=0.02),
        config,
    )
    pd.testing.assert_series_equal(
        baseline.equity_curve, with_thresholds.equity_curve
    )
    assert len(baseline.trades) == len(with_thresholds.trades)


def test_strategy_still_conforms_to_protocol_with_kwargs():
    strategy = MomoBtcV2(stop_loss_pct=0.03, take_profit_pct=0.06)
    assert isinstance(strategy, Strategy)
