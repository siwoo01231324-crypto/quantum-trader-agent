"""Unit tests for triple_barrier_label."""
import numpy as np
import pandas as pd
import pytest

from src.ml.labeling import triple_barrier_label


def _make_prices(values, freq="1h", start="2024-01-01"):
    idx = pd.date_range(start, periods=len(values), freq=freq)
    return pd.Series(values, index=idx, name="close", dtype=float)


def _make_events(entry_times, sides, t1_times):
    return pd.DataFrame(
        {"side": sides, "t1": pd.to_datetime(t1_times)},
        index=pd.to_datetime(entry_times),
    )


class TestTpBarrier:
    def test_tp_fires_first(self):
        # Long trade: price rises quickly to hit tp
        prices = _make_prices([100, 102, 104, 106])
        events = _make_events(
            ["2024-01-01 00:00"],
            [1],
            ["2024-01-01 03:00"],
        )
        result = triple_barrier_label(prices, events, tp=0.03, sl=0.10)
        assert result.loc["2024-01-01", "barrier"] == "tp"
        assert result.loc["2024-01-01", "label"] == 1

    def test_short_tp_fires(self):
        # Short trade: price drops to hit tp
        prices = _make_prices([100, 98, 96, 94])
        events = _make_events(
            ["2024-01-01 00:00"],
            [-1],
            ["2024-01-01 03:00"],
        )
        result = triple_barrier_label(prices, events, tp=0.03, sl=0.10)
        assert result.loc["2024-01-01", "barrier"] == "tp"
        assert result.loc["2024-01-01", "label"] == 1


class TestSlBarrier:
    def test_sl_fires_first(self):
        # Long trade: price drops to hit sl
        prices = _make_prices([100, 98, 96, 106])
        events = _make_events(
            ["2024-01-01 00:00"],
            [1],
            ["2024-01-01 03:00"],
        )
        result = triple_barrier_label(prices, events, tp=0.10, sl=0.03)
        assert result.loc["2024-01-01", "barrier"] == "sl"
        assert result.loc["2024-01-01", "label"] == 0


class TestT1Barrier:
    def test_t1_fires_when_no_other_barrier(self):
        # Price barely moves — neither tp nor sl reached
        prices = _make_prices([100, 100.5, 100.8, 101.0])
        events = _make_events(
            ["2024-01-01 00:00"],
            [1],
            ["2024-01-01 03:00"],
        )
        result = triple_barrier_label(prices, events, tp=0.05, sl=0.05)
        assert result.loc["2024-01-01", "barrier"] == "t1"


class TestCosts:
    def test_costs_reduce_net_ret(self):
        prices = _make_prices([100, 105])
        events = _make_events(["2024-01-01 00:00"], [1], ["2024-01-01 01:00"])
        no_cost = triple_barrier_label(prices, events, tp=0.03, sl=0.10, costs_bps=0.0)
        with_cost = triple_barrier_label(prices, events, tp=0.03, sl=0.10, costs_bps=10.0)
        assert with_cost.loc["2024-01-01", "ret"] < no_cost.loc["2024-01-01", "ret"]

    def test_high_costs_flip_label(self):
        # Small profit but huge cost → label 0
        prices = _make_prices([100, 100.1, 105])
        events = _make_events(["2024-01-01 00:00"], [1], ["2024-01-01 02:00"])
        # tp at 0.05 → exit at bar 2 (price 105), raw ret = 0.05
        # costs_bps=600 → costs_frac=0.06 > ret → net_ret < 0 → label 0
        result = triple_barrier_label(prices, events, tp=0.04, sl=0.10, costs_bps=600)
        assert result.loc["2024-01-01", "label"] == 0


class TestLookaheadGuard:
    def test_t_touch_strictly_after_entry(self):
        prices = _make_prices([100, 110, 120, 130])
        events = _make_events(["2024-01-01 00:00"], [1], ["2024-01-01 03:00"])
        result = triple_barrier_label(prices, events, tp=0.05, sl=0.10)
        entry_ts = pd.Timestamp("2024-01-01 00:00")
        assert result.loc["2024-01-01", "t_touch"] > entry_ts


class TestPricesCoverage:
    def test_raises_if_prices_do_not_cover_events(self):
        prices = _make_prices([100, 101, 102])  # ends at hour 2
        # t1 is at hour 10 — beyond price coverage
        events = _make_events(
            ["2024-01-01 00:00"],
            [1],
            ["2024-01-01 10:00"],
        )
        with pytest.raises(ValueError):
            triple_barrier_label(prices, events, tp=0.05, sl=0.05)
