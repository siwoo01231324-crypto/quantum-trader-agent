"""Tests for src/backtest/swing/strategies.py (issue #99 iter 2 + iter 3).

Each strategy function is tested for:
  - Correct signal shape and range
  - Causal (no lookahead) via assert_no_lookahead where applicable
  - Edge cases (missing data, constant price, etc.)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.backtest.swing.atr import wilder_atr
from src.backtest.swing.strategies import (
    s1_tsmom,
    s2_donchian,
    s2_donchian_atr_stop,
    s2_donchian_hard_rr,
    s2_donchian_voltarget,
    s2c_x_s4_composite,
    s3_ema_pullback,
    s4_funding_both,
    s4_funding_carry,
    s5_pairs,
)


@pytest.fixture
def ohlcv_4h() -> pd.DataFrame:
    """500 bars of 4h synthetic OHLCV."""
    rng = np.random.default_rng(42)
    idx = pd.date_range("2023-01-01", periods=500, freq="4h", tz="UTC")
    close = pd.Series(
        30000.0 + rng.normal(scale=100, size=500).cumsum(), index=idx
    )
    volume = pd.Series(rng.uniform(1.0, 100.0, size=500), index=idx)
    return pd.DataFrame(
        {
            "open": close.shift(1).bfill(),
            "high": close * (1 + np.abs(rng.normal(scale=0.002, size=500))),
            "low": close * (1 - np.abs(rng.normal(scale=0.002, size=500))),
            "close": close,
            "volume": volume,
        }
    )


# --------- S1: TSMOM -------------------------------------------------------


class TestS1Tsmom:
    def test_ascending_series_all_long(self) -> None:
        """Strictly ascending close -> all returns positive -> all long after warmup."""
        idx = pd.date_range("2023-01-01", periods=50, freq="4h", tz="UTC")
        df = pd.DataFrame(
            {"close": np.linspace(100.0, 200.0, 50)}, index=idx
        )
        sig = s1_tsmom(df, lookback=6)
        # After lookback + shift warmup (7 bars), all should be 1
        assert (sig.iloc[7:] == 1).all()

    def test_descending_series_all_flat(self) -> None:
        idx = pd.date_range("2023-01-01", periods=50, freq="4h", tz="UTC")
        df = pd.DataFrame(
            {"close": np.linspace(200.0, 100.0, 50)}, index=idx
        )
        sig = s1_tsmom(df, lookback=6)
        assert (sig.iloc[7:] == 0).all()

    def test_signal_range(self, ohlcv_4h: pd.DataFrame) -> None:
        sig = s1_tsmom(ohlcv_4h)
        assert set(sig.unique()).issubset({0, 1})

    def test_causal(self, ohlcv_4h: pd.DataFrame) -> None:
        """Verify s1 is causal: appending one bar does not change earlier signals."""
        short_df = ohlcv_4h.iloc[:-1]
        full_df = ohlcv_4h
        short_sig = s1_tsmom(short_df, lookback=6)
        full_sig = s1_tsmom(full_df, lookback=6)
        pd.testing.assert_series_equal(
            short_sig, full_sig.iloc[: len(short_sig)], check_names=False
        )

    def test_name(self, ohlcv_4h: pd.DataFrame) -> None:
        sig = s1_tsmom(ohlcv_4h)
        assert sig.name == "s1_signal"


# --------- S2: Donchian Breakout -------------------------------------------


class TestS2Donchian:
    def test_breakout_entry(self) -> None:
        """Price breaking above 20-bar high -> enter long."""
        idx = pd.date_range("2023-01-01", periods=30, freq="4h", tz="UTC")
        # Flat for 20 bars, then spike up
        prices = [100.0] * 20 + [105.0] + [100.0] * 9
        df = pd.DataFrame({"close": prices}, index=idx)
        sig = s2_donchian(df, entry_lookback=20, exit_lookback=10)
        # After the spike (bar 20), should enter
        assert sig.iloc[20] == 1

    def test_exit_on_low(self) -> None:
        """Price breaking below 10-bar low -> exit."""
        idx = pd.date_range("2023-01-01", periods=40, freq="4h", tz="UTC")
        prices = [100.0] * 20 + [110.0] * 10 + [90.0] * 10
        df = pd.DataFrame({"close": prices}, index=idx)
        sig = s2_donchian(df, entry_lookback=20, exit_lookback=10)
        # After dropping below 10-bar low, should be flat
        assert sig.iloc[-1] == 0

    def test_signal_range(self, ohlcv_4h: pd.DataFrame) -> None:
        sig = s2_donchian(ohlcv_4h)
        assert set(sig.unique()).issubset({0, 1})

    def test_causal(self, ohlcv_4h: pd.DataFrame) -> None:
        """Donchian uses shift(1) on rolling max/min -> causal."""
        short_df = ohlcv_4h.iloc[:-1]
        full_df = ohlcv_4h
        short_sig = s2_donchian(short_df, entry_lookback=20, exit_lookback=10)
        full_sig = s2_donchian(full_df, entry_lookback=20, exit_lookback=10)
        pd.testing.assert_series_equal(
            short_sig, full_sig.iloc[: len(short_sig)], check_names=False
        )


# --------- S3: EMA Pullback + RSI ------------------------------------------


class TestS3EmaPullback:
    def test_uptrend_oversold_entry(self) -> None:
        """In uptrend with RSI dip -> enter. Trend break -> exit."""
        idx = pd.date_range("2023-01-01", periods=300, freq="4h", tz="UTC")
        # Strong uptrend for EMA warmup, then a sharp crash to trigger RSI < 30
        # while price stays above EMA (crash from well above EMA).
        prices = np.zeros(300)
        # Phase 1: steady climb from 100 to 400 over first 200 bars
        prices[:200] = np.linspace(100.0, 400.0, 200)
        # Phase 2: sharp drop of ~8% over 10 bars (RSI crashes), but
        # from 400 -> 370, still well above EMA50 which lags around 300
        prices[200:210] = np.linspace(400.0, 370.0, 10)
        # Phase 3: recovery
        prices[210:] = np.linspace(372.0, 500.0, 90)
        df = pd.DataFrame({"close": prices}, index=idx)
        sig = s3_ema_pullback(df, ema_trend=50, rsi_lookback=14, rsi_threshold=40.0)
        # Should have at least one entry after the dip triggers RSI < 40
        assert sig.sum() > 0

    def test_no_signal_in_downtrend(self) -> None:
        """Strictly descending series -> no long positions."""
        idx = pd.date_range("2023-01-01", periods=300, freq="4h", tz="UTC")
        df = pd.DataFrame(
            {"close": np.linspace(500.0, 100.0, 300)}, index=idx
        )
        sig = s3_ema_pullback(df, ema_trend=50)
        # Below EMA -> no entries
        assert sig.iloc[60:].sum() == 0  # after warmup

    def test_signal_range(self, ohlcv_4h: pd.DataFrame) -> None:
        sig = s3_ema_pullback(ohlcv_4h)
        assert set(sig.unique()).issubset({0, 1})

    def test_causal(self, ohlcv_4h: pd.DataFrame) -> None:
        """Verify s3 is causal: appending one bar does not change earlier signals."""
        short_df = ohlcv_4h.iloc[:-1]
        full_df = ohlcv_4h
        short_sig = s3_ema_pullback(short_df, ema_trend=50, rsi_lookback=14, rsi_threshold=30.0)
        full_sig = s3_ema_pullback(full_df, ema_trend=50, rsi_lookback=14, rsi_threshold=30.0)
        pd.testing.assert_series_equal(
            short_sig, full_sig.iloc[: len(short_sig)], check_names=False
        )


# --------- S4: Funding Carry -----------------------------------------------


class TestS4FundingCarry:
    def test_missing_funding_rate_returns_unavailable(self) -> None:
        """Without _funding_rate column -> DATA_UNAVAILABLE signal name."""
        idx = pd.date_range("2023-01-01", periods=50, freq="4h", tz="UTC")
        df = pd.DataFrame({"close": np.ones(50)}, index=idx)
        sig = s4_funding_carry(df)
        assert "unavailable" in sig.name
        assert (sig == 0).all()

    def test_negative_funding_triggers_long(self) -> None:
        """Strongly negative funding -> carry signal (long)."""
        idx = pd.date_range("2023-01-01", periods=20, freq="4h", tz="UTC")
        funding = [-0.001] * 20  # -0.1% -> well below threshold
        df = pd.DataFrame(
            {"close": np.ones(20), "_funding_rate": funding}, index=idx
        )
        sig = s4_funding_carry(df)
        assert sig.name == "s4_signal"
        # After shift(1), bar 1 onward should be long
        assert (sig.iloc[1:] == 1).all()

    def test_positive_funding_stays_flat(self) -> None:
        idx = pd.date_range("2023-01-01", periods=20, freq="4h", tz="UTC")
        funding = [0.001] * 20
        df = pd.DataFrame(
            {"close": np.ones(20), "_funding_rate": funding}, index=idx
        )
        sig = s4_funding_carry(df)
        assert (sig == 0).all()

    def test_signal_range(self) -> None:
        idx = pd.date_range("2023-01-01", periods=50, freq="4h", tz="UTC")
        rng = np.random.default_rng(42)
        df = pd.DataFrame(
            {
                "close": np.ones(50),
                "_funding_rate": rng.normal(scale=0.001, size=50),
            },
            index=idx,
        )
        sig = s4_funding_carry(df)
        assert set(sig.unique()).issubset({0, 1})


# --------- S5: Pairs Trading -----------------------------------------------


class TestS5Pairs:
    def test_z_score_entry_exit(self) -> None:
        """Synthetic diverging prices -> entry; convergence -> exit."""
        n = 300
        idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
        rng = np.random.default_rng(42)
        # BTC with upward drift, ETH with mild random walk -> ratio diverges
        btc_prices = 30000 * np.exp(
            np.cumsum(rng.normal(loc=0.002, scale=0.01, size=n))
        )
        eth_prices = 2000 * np.exp(
            np.cumsum(rng.normal(loc=0.0, scale=0.005, size=n))
        )
        btc_df = pd.DataFrame({"close": btc_prices}, index=idx)
        eth_df = pd.DataFrame({"close": eth_prices}, index=idx)
        result = s5_pairs(btc_df, eth_df, lookback=30, z_entry=1.5, z_exit=0.5)
        assert "btc_pos" in result.columns
        assert "eth_pos" in result.columns
        # Should eventually enter a position due to ratio divergence
        assert result["btc_pos"].abs().sum() > 0

    def test_output_shape(self) -> None:
        n = 100
        idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
        rng = np.random.default_rng(42)
        btc_df = pd.DataFrame(
            {"close": 30000 + rng.normal(scale=100, size=n).cumsum()},
            index=idx,
        )
        eth_df = pd.DataFrame(
            {"close": 2000 + rng.normal(scale=10, size=n).cumsum()},
            index=idx,
        )
        result = s5_pairs(btc_df, eth_df, lookback=20)
        assert set(result.columns) == {"btc_pos", "eth_pos"}
        assert set(result["btc_pos"].unique()).issubset({-1, 0, 1})
        assert set(result["eth_pos"].unique()).issubset({-1, 0, 1})

    def test_positions_are_opposite(self) -> None:
        """When in a position, BTC and ETH should be opposite."""
        n = 200
        idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
        rng = np.random.default_rng(7)
        btc_df = pd.DataFrame(
            {"close": 30000 + rng.normal(scale=300, size=n).cumsum()},
            index=idx,
        )
        eth_df = pd.DataFrame(
            {"close": 2000 + rng.normal(scale=20, size=n).cumsum()},
            index=idx,
        )
        result = s5_pairs(btc_df, eth_df, lookback=20, z_entry=1.5)
        # Where either is nonzero, they must be opposite
        active = result[(result["btc_pos"] != 0) | (result["eth_pos"] != 0)]
        if len(active) > 0:
            assert (active["btc_pos"] + active["eth_pos"] == 0).all()

    def test_flat_prices_no_signal(self) -> None:
        """Constant prices -> z-score = 0 -> no entry."""
        n = 100
        idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
        btc_df = pd.DataFrame({"close": np.full(n, 30000.0)}, index=idx)
        eth_df = pd.DataFrame({"close": np.full(n, 2000.0)}, index=idx)
        result = s5_pairs(btc_df, eth_df, lookback=20)
        assert (result["btc_pos"] == 0).all()
        assert (result["eth_pos"] == 0).all()


# --------- Iter 3: ATR -------------------------------------------------------


class TestAtrWilderKnownValue:
    def test_atr_wilder_known_value(self) -> None:
        """Wilder ATR on a simple series with known true range values."""
        idx = pd.date_range("2023-01-01", periods=20, freq="4h", tz="UTC")
        # Construct data where true range is always 10.0
        # high=110, low=100, close=105 (prev close=105)
        # TR = max(110-100, |110-105|, |100-105|) = max(10, 5, 5) = 10
        df = pd.DataFrame(
            {
                "high": np.full(20, 110.0),
                "low": np.full(20, 100.0),
                "close": np.full(20, 105.0),
            },
            index=idx,
        )
        atr = wilder_atr(df, period=14)
        assert atr.name == "atr"
        # After warmup, ATR should converge to 10.0
        # With constant TR=10, the EWM converges to 10.0
        assert abs(atr.iloc[-1] - 10.0) < 0.5


# --------- Iter 3: S2a ATR Stop -----------------------------------------------


class TestS2aAtrStop:
    def test_s2_atr_stop_triggers_exit(self) -> None:
        """S2a: ATR trailing stop should exit when price drops > 2*ATR below peak."""
        n = 60
        idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
        # Phase 1: flat for 20 bars at 100
        # Phase 2: spike to 200 (triggers Donchian entry)
        # Phase 3: climb to 250 (trailing high updates)
        # Phase 4: crash to 100 (triggers ATR stop)
        prices = np.zeros(n)
        prices[:20] = 100.0
        prices[20] = 200.0  # Donchian entry
        prices[21:30] = np.linspace(210.0, 250.0, 9)  # climb
        prices[30:] = np.linspace(100.0, 80.0, 30)  # crash

        # Build OHLCV with realistic high/low
        high = prices * 1.01
        low = prices * 0.99
        df = pd.DataFrame(
            {"open": prices, "high": high, "low": low, "close": prices, "volume": np.ones(n)},
            index=idx,
        )
        sig = s2_donchian_atr_stop(df, entry_lookback=20, exit_lookback=10, atr_period=5, atr_multiplier=2.0)
        assert sig.name == "s2a_signal"
        # Should enter after spike and exit after crash
        assert sig.iloc[21] == 1  # in position after entry
        assert sig.iloc[-1] == 0  # exited after crash

    def test_signal_range(self, ohlcv_4h: pd.DataFrame) -> None:
        sig = s2_donchian_atr_stop(ohlcv_4h, atr_period=14)
        assert set(sig.unique()).issubset({0, 1})


# --------- Iter 3: S2b Hard R:R ----------------------------------------------


class TestS2bHardRR:
    def test_s2_hard_rr_take_profit(self) -> None:
        """S2b: Take profit at +7% should close position."""
        n = 50
        idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
        prices = np.zeros(n)
        prices[:20] = 100.0
        prices[20] = 105.0  # Donchian entry
        # Gradual rise to trigger +7% take profit (entry ~105, TP = 105*1.07 = 112.35)
        prices[21:30] = np.linspace(106.0, 115.0, 9)
        prices[30:] = 115.0

        df = pd.DataFrame(
            {
                "open": prices,
                "high": prices * 1.005,
                "low": prices * 0.995,
                "close": prices,
                "volume": np.ones(n),
            },
            index=idx,
        )
        sig = s2_donchian_hard_rr(df, entry_lookback=20, exit_lookback=10, stop_pct=0.01, tp_pct=0.07)
        assert sig.name == "s2b_signal"
        # Should enter after breakout
        assert sig.iloc[20] == 1
        # After price goes above entry+7%, should exit
        # Find where price first exceeds 105*1.07 = 112.35
        exit_bar = None
        for i in range(21, 50):
            if prices[i] >= 105.0 * 1.07:
                exit_bar = i
                break
        assert exit_bar is not None
        assert sig.iloc[exit_bar] == 0

    def test_s2_hard_rr_stop_loss(self) -> None:
        """S2b: Stop loss at -1% should close position."""
        n = 50
        idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
        prices = np.zeros(n)
        prices[:20] = 100.0
        prices[20] = 105.0  # Donchian entry
        # Drop to trigger -1% stop (entry ~105, SL = 105*0.99 = 103.95)
        prices[21:25] = 103.0  # below stop
        prices[25:] = 110.0

        df = pd.DataFrame(
            {
                "open": prices,
                "high": prices * 1.005,
                "low": prices * 0.995,
                "close": prices,
                "volume": np.ones(n),
            },
            index=idx,
        )
        sig = s2_donchian_hard_rr(df, entry_lookback=20, exit_lookback=10, stop_pct=0.01, tp_pct=0.07)
        # Should exit after drop below stop
        assert sig.iloc[21] == 0


# --------- Iter 3: S2c Vol-Target ---------------------------------------------


class TestS2cVolTarget:
    def test_s2_voltarget_caps_at_1(self) -> None:
        """S2c: position size should be capped at 1.0."""
        n = 200
        idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
        rng = np.random.default_rng(42)
        close = 30000.0 + rng.normal(scale=100, size=n).cumsum()
        df = pd.DataFrame(
            {
                "open": close,
                "high": close * (1 + np.abs(rng.normal(scale=0.002, size=n))),
                "low": close * (1 - np.abs(rng.normal(scale=0.002, size=n))),
                "close": close,
                "volume": rng.uniform(1.0, 100.0, size=n),
            },
            index=idx,
        )
        signal, pos_size = s2_donchian_voltarget(df, vol_target=0.15, vol_lookback=30)
        assert signal.name == "s2c_signal"
        assert pos_size.name == "s2c_pos_size"
        # Position size must be <= 1.0
        assert (pos_size <= 1.0 + 1e-10).all()
        # Position size must be >= 0.0
        assert (pos_size >= -1e-10).all()


# --------- Iter 3: S4a Bidirectional Funding ----------------------------------


class TestS4aFundingBoth:
    def test_s4_funding_long_when_negative(self) -> None:
        """S4a: strongly negative funding -> long."""
        idx = pd.date_range("2023-01-01", periods=20, freq="4h", tz="UTC")
        funding = [-0.001] * 20  # well below threshold_neg
        df = pd.DataFrame(
            {"close": np.ones(20), "_funding_rate": funding}, index=idx
        )
        sig = s4_funding_both(df)
        assert sig.name == "s4a_signal"
        # After shift(1), bar 1 onward should be long (+1)
        assert (sig.iloc[1:] == 1).all()

    def test_s4_both_short_when_positive(self) -> None:
        """S4a: strongly positive funding -> short."""
        idx = pd.date_range("2023-01-01", periods=20, freq="4h", tz="UTC")
        funding = [0.002] * 20  # well above threshold_pos (0.0005)
        df = pd.DataFrame(
            {"close": np.ones(20), "_funding_rate": funding}, index=idx
        )
        sig = s4_funding_both(df)
        # After shift(1), bar 1 onward should be short (-1)
        assert (sig.iloc[1:] == -1).all()

    def test_s4_both_flat_near_zero(self) -> None:
        """S4a: funding near zero -> flat."""
        idx = pd.date_range("2023-01-01", periods=20, freq="4h", tz="UTC")
        funding = [0.00001] * 20  # between thresholds
        df = pd.DataFrame(
            {"close": np.ones(20), "_funding_rate": funding}, index=idx
        )
        sig = s4_funding_both(df)
        assert (sig.iloc[1:] == 0).all()

    def test_missing_funding_rate_returns_unavailable(self) -> None:
        """S4a: Without _funding_rate -> DATA_UNAVAILABLE signal name."""
        idx = pd.date_range("2023-01-01", periods=20, freq="4h", tz="UTC")
        df = pd.DataFrame({"close": np.ones(20)}, index=idx)
        sig = s4_funding_both(df)
        assert "unavailable" in sig.name


# --------- Iter 4: W3 S2c x S4 Composite ------------------------------------


class TestS2cXS4Composite:
    def test_zero_when_s4_zero(self) -> None:
        """W3: If funding is positive (S4=0), composite should be 0 even if S2=1."""
        n = 100
        idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
        # Strong uptrend to trigger Donchian entry
        prices = np.linspace(100.0, 300.0, n)
        funding = [0.001] * n  # positive funding -> S4 = 0
        df = pd.DataFrame(
            {
                "open": prices,
                "high": prices * 1.005,
                "low": prices * 0.995,
                "close": prices,
                "volume": np.ones(n),
                "_funding_rate": funding,
            },
            index=idx,
        )
        signal, pos_size = s2c_x_s4_composite(df)
        assert signal.name == "w3_signal"
        assert pos_size.name == "w3_pos_size"
        # S4 is all zero -> composite must be all zero
        assert (signal == 0).all()

    def test_zero_when_s2_zero(self) -> None:
        """W3: If Donchian is flat (S2=0), composite should be 0 even if S4=1."""
        n = 100
        idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
        # Flat prices -> no Donchian breakout
        prices = np.full(n, 100.0)
        funding = [-0.001] * n  # negative funding -> S4 = 1
        df = pd.DataFrame(
            {
                "open": prices,
                "high": prices * 1.001,
                "low": prices * 0.999,
                "close": prices,
                "volume": np.ones(n),
                "_funding_rate": funding,
            },
            index=idx,
        )
        signal, pos_size = s2c_x_s4_composite(df)
        # S2 is all zero -> composite must be all zero
        assert (signal == 0).all()

    def test_long_when_both_one(self) -> None:
        """W3: When both S2=1 and S4=1, composite should be 1."""
        n = 200
        idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
        # Strong uptrend to trigger Donchian
        prices = np.linspace(100.0, 500.0, n)
        # Negative funding -> S4 = 1
        funding = [-0.001] * n
        df = pd.DataFrame(
            {
                "open": prices,
                "high": prices * 1.005,
                "low": prices * 0.995,
                "close": prices,
                "volume": np.ones(n),
                "_funding_rate": funding,
            },
            index=idx,
        )
        signal, pos_size = s2c_x_s4_composite(df, vol_lookback=30)
        # After warmup (Donchian entry_lookback=20 + shift), should have some 1s
        assert signal.sum() > 0
        # After vol warmup (vol_lookback + shift), pos_size should be > 0
        # where signal is 1
        after_warmup = signal.iloc[35:]  # past both Donchian (20) and vol (30) warmup
        active_after_warmup = after_warmup[after_warmup == 1]
        assert len(active_after_warmup) > 0
        assert (pos_size[active_after_warmup.index] > 0).all()

    def test_size_capped_at_one(self) -> None:
        """W3: Position size should never exceed 1.0."""
        n = 200
        idx = pd.date_range("2023-01-01", periods=n, freq="4h", tz="UTC")
        rng = np.random.default_rng(42)
        close = 30000.0 + rng.normal(scale=100, size=n).cumsum()
        funding = [-0.001] * n
        df = pd.DataFrame(
            {
                "open": close,
                "high": close * (1 + np.abs(rng.normal(scale=0.002, size=n))),
                "low": close * (1 - np.abs(rng.normal(scale=0.002, size=n))),
                "close": close,
                "volume": rng.uniform(1.0, 100.0, size=n),
                "_funding_rate": funding,
            },
            index=idx,
        )
        signal, pos_size = s2c_x_s4_composite(df, vol_target=0.15, vol_lookback=30)
        assert (pos_size <= 1.0 + 1e-10).all()
        assert (pos_size >= -1e-10).all()
