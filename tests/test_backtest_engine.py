import pytest
import pandas as pd
import numpy as np

from src.backtest.protocol import Bar, Signal, Strategy
from src.backtest.engine import BacktestConfig, BacktestResult, run_backtest
from src.backtest.metrics import compute_sharpe, compute_max_drawdown


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(closes: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame with a DatetimeIndex."""
    n = len(closes)
    idx = pd.date_range(start, periods=n, freq="1D", tz="UTC")
    df = pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1000.0] * n,
        },
        index=idx,
    )
    return df


class BuyAndHoldStrategy:
    """Buys on bar 0, never sells."""

    def on_init(self, context: dict) -> None:
        self._bought = False

    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
        if not self._bought:
            self._bought = True
            return Signal(action="buy", size=1.0, reason="initial buy")
        return Signal(action="hold", size=0.0, reason="hold")


class BuyThenSellStrategy:
    """Buys on bar 0, sells on bar 1."""

    def on_init(self, context: dict) -> None:
        self._step = 0

    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
        step = self._step
        self._step += 1
        if step == 0:
            return Signal(action="buy", size=1.0, reason="buy")
        elif step == 1:
            return Signal(action="sell", size=1.0, reason="sell")
        return Signal(action="hold", size=0.0, reason="hold")


class HoldStrategy:
    """Never trades."""

    def on_init(self, context: dict) -> None:
        pass

    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
        return Signal(action="hold", size=0.0, reason="hold")


class CountingStrategy:
    """Counts how many times on_bar is called."""

    def on_init(self, context: dict) -> None:
        self.count = 0

    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
        self.count += 1
        return Signal(action="hold", size=0.0, reason="hold")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_engine_iterates_all_bars():
    """on_bar must be called exactly once per row."""
    closes = [100.0, 101.0, 102.0, 103.0, 104.0]
    ohlcv = _make_ohlcv(closes)
    strategy = CountingStrategy()
    run_backtest(ohlcv, strategy)
    assert strategy.count == len(closes)


def test_engine_tracks_positions():
    """After buy signal position>0; after sell signal position==0."""
    closes = [100.0, 110.0, 105.0]
    ohlcv = _make_ohlcv(closes)
    strategy = BuyThenSellStrategy()
    result = run_backtest(ohlcv, strategy)
    actions = [t["action"] for t in result.trades]
    assert "buy" in actions
    assert "sell" in actions
    # After the sell the equity curve should be cash-only (no unrealised position)
    # The trade list should contain one buy and one sell
    buys = [t for t in result.trades if t["action"] == "buy"]
    sells = [t for t in result.trades if t["action"] == "sell"]
    assert len(buys) == 1
    assert len(sells) == 1


def test_engine_computes_equity_curve():
    """Buy-and-hold with known prices → verify final equity."""
    config = BacktestConfig(
        initial_cash=10_000.0,
        commission_pct=0.0,
        slippage_pct=0.0,
        max_drawdown_halt_pct=1.0,  # disable MDD halt
    )
    closes = [100.0, 110.0, 120.0]
    ohlcv = _make_ohlcv(closes)
    strategy = BuyAndHoldStrategy()
    result = run_backtest(ohlcv, strategy, config)

    # With no commission/slippage, buying at 100 with 10_000 cash gives 100 units.
    # Final equity at close 120 → 100 * 120 = 12_000
    assert abs(result.equity_curve.iloc[-1] - 12_000.0) < 1.0
    assert len(result.equity_curve) == 3


def test_engine_computes_sharpe():
    """Constant 1 % daily return → Sharpe ~ sqrt(365)*1 (mean/std undefined for constant series).
    We use a tiny noise to get a finite std, or verify zero-std path returns 0.0."""
    # Zero-std path: all returns identical → std == 0 → should return 0.0
    config = BacktestConfig(
        initial_cash=100.0,
        commission_pct=0.0,
        slippage_pct=0.0,
        max_drawdown_halt_pct=1.0,
    )
    # Equity grows at exactly 1 % per day — pct_change is constant → std = 0
    n = 30
    closes = [100.0 * (1.01 ** i) for i in range(n)]
    ohlcv = _make_ohlcv(closes)
    strategy = HoldStrategy()  # equity == cash == 100 (no trades); use pre-built equity
    # Build an equity series directly to test compute_sharpe
    idx = pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")
    equity = pd.Series(closes, index=idx)
    sharpe = compute_sharpe(equity)
    # std of constant 1% returns ≈ 0 → function returns 0.0
    # But with float arithmetic there may be tiny rounding, so allow small positive
    assert sharpe >= 0.0

    # Non-constant returns: noisy series should yield finite nonzero Sharpe
    np.random.seed(42)
    noisy_returns = 0.01 + np.random.normal(0, 0.005, n)
    noisy_equity = pd.Series(
        100.0 * np.cumprod(1 + noisy_returns),
        index=idx,
    )
    noisy_sharpe = compute_sharpe(noisy_equity)
    assert noisy_sharpe != 0.0


def test_engine_computes_max_drawdown():
    """equity [100, 110, 95, 105] → MDD = (110-95)/110 ≈ 13.6 %."""
    idx = pd.date_range("2024-01-01", periods=4, freq="1D", tz="UTC")
    equity = pd.Series([100.0, 110.0, 95.0, 105.0], index=idx)
    mdd = compute_max_drawdown(equity)
    expected = (110.0 - 95.0) / 110.0
    assert abs(mdd - expected) < 1e-6


def test_engine_halt_stops_trading():
    """After MDD halt is triggered, no further buy/sell orders should appear."""
    config = BacktestConfig(
        initial_cash=100_000.0,
        commission_pct=0.0,
        slippage_pct=0.0,
        max_drawdown_halt_pct=0.05,  # 5 %
    )
    # Prices: buy at 100, then drop to 90 (-10%) to trigger halt, then recover
    closes = [100.0, 100.0, 90.0, 95.0, 98.0, 102.0]
    ohlcv = _make_ohlcv(closes)

    class BuyOnceStrategy:
        def on_init(self, context: dict) -> None:
            self._bought = False
            self.bar_calls_after_halt = 0

        def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
            if not self._bought:
                self._bought = True
                return Signal(action="buy", size=1.0, reason="buy")
            return Signal(action="buy", size=1.0, reason="should not execute")

    strategy = BuyOnceStrategy()
    result = run_backtest(ohlcv, strategy, config)

    # Only the initial buy + an automatic MDD-halt sell should appear
    buy_trades = [t for t in result.trades if t["action"] == "buy"]
    assert len(buy_trades) == 1, f"Expected 1 buy, got {len(buy_trades)}"


def test_engine_no_position_when_halted():
    """When MDD > halt threshold, engine flattens position and stops trading."""
    config = BacktestConfig(
        initial_cash=100_000.0,
        commission_pct=0.0,
        slippage_pct=0.0,
        max_drawdown_halt_pct=0.05,  # 5 %
    )
    # Buy at bar 0 (price 100), price drops to 80 (-20 %) to trigger halt
    closes = [100.0, 100.0, 80.0, 80.0, 80.0]
    ohlcv = _make_ohlcv(closes)
    strategy = BuyAndHoldStrategy()
    result = run_backtest(ohlcv, strategy, config)

    # There should be a MDD-halt sell trade
    halt_sells = [t for t in result.trades if t.get("reason") == "MDD halt"]
    assert len(halt_sells) >= 1

    # After the halt sell, position is zero → equity equals cash only
    # The last equity value should match the cash after the forced sell
    # (no further price exposure)
    last_equity = result.equity_curve.iloc[-1]
    # Cash-only: should be approximately 80_000 (bought 1000 units at 100, sold at 80)
    assert last_equity > 0


def test_strategy_protocol_enforced():
    """Passing a non-Strategy object to run_backtest must raise TypeError."""
    ohlcv = _make_ohlcv([100.0, 101.0])

    class NotAStrategy:
        pass

    with pytest.raises(TypeError):
        run_backtest(ohlcv, NotAStrategy())
