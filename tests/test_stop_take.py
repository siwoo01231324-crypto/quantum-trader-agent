"""TDD tests for src/backtest/risk/stop_take.py — Issue #147.

Edge cases covered (6+):
  1. Normal stop-loss hit (low touches stop level intra-bar)
  2. Normal take-profit hit (high touches take level intra-bar)
  3. Gap-down: bar open below stop → exit at open, no extra slippage
  4. Hit-both-same-bar conservative: stop wins over take
  5. Stop triggered after several healthy bars
  6. signal_exit fires before stop/take
  7. No exit: position survives all bars (returns all-None result)
  8. ATR-proxy: wider stop survives volatility that 1% stop would catch
  9. Config validation: invalid pct raises ValueError
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.backtest.risk.stop_take import (
    StopTakeConfig,
    StopTakeResult,
    simulate_stop_take,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bars(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(opens), freq="1min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes},
        index=idx,
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestStopTakeConfig:
    def test_invalid_stop_loss_zero(self):
        with pytest.raises(ValueError, match="stop_loss_pct"):
            StopTakeConfig(stop_loss_pct=0.0)

    def test_invalid_stop_loss_negative(self):
        with pytest.raises(ValueError, match="stop_loss_pct"):
            StopTakeConfig(stop_loss_pct=-0.01)

    def test_invalid_take_profit_zero(self):
        with pytest.raises(ValueError, match="take_profit_pct"):
            StopTakeConfig(take_profit_pct=0.0)

    def test_invalid_slippage_negative(self):
        with pytest.raises(ValueError, match="slippage_pct"):
            StopTakeConfig(stop_loss_pct=0.01, slippage_pct=-0.001)

    def test_valid_config(self):
        cfg = StopTakeConfig(stop_loss_pct=0.01, take_profit_pct=0.07)
        assert cfg.stop_loss_pct == 0.01
        assert cfg.take_profit_pct == 0.07


class TestSimulateStopTake:
    # 1. Normal stop-loss hit intra-bar
    def test_normal_stop_hit(self):
        entry = 100.0
        cfg = StopTakeConfig(stop_loss_pct=0.01)  # stop at 99.0
        bars = _bars(
            opens=[100.5, 100.3, 99.5],
            highs=[101.0, 100.8, 100.0],
            lows=[100.0, 100.0, 98.8],   # bar 2: low=98.8 < 99.0
            closes=[100.5, 100.3, 99.0],
        )
        result = simulate_stop_take(entry, bars, cfg)
        assert result.reason == "stop"
        assert result.triggered_at == bars.index[2]
        # exit_price = 99.0 * (1 - 0.0005)
        assert abs(result.exit_price - 99.0 * (1 - 0.0005)) < 1e-9

    # 2. Normal take-profit hit intra-bar
    def test_normal_take_hit(self):
        entry = 100.0
        cfg = StopTakeConfig(take_profit_pct=0.07)  # take at 107.0
        bars = _bars(
            opens=[100.5, 103.0, 106.5],
            highs=[102.0, 105.0, 107.5],  # bar 2: high=107.5 >= 107.0
            lows=[100.0, 102.5, 106.0],
            closes=[101.0, 104.0, 107.0],
        )
        result = simulate_stop_take(entry, bars, cfg)
        assert result.reason == "take"
        assert result.triggered_at == bars.index[2]
        # exit_price = 107.0 * (1 + 0.0005)
        assert abs(result.exit_price - 107.0 * (1 + 0.0005)) < 1e-9

    # 3. Gap-down: bar open already below stop level
    def test_gap_down_exit_at_open(self):
        entry = 100.0
        cfg = StopTakeConfig(stop_loss_pct=0.01)  # stop at 99.0
        # Second bar opens at 98.0 (gap below stop)
        bars = _bars(
            opens=[100.5, 98.0],
            highs=[101.0, 99.0],
            lows=[100.0, 97.5],
            closes=[100.5, 98.5],
        )
        result = simulate_stop_take(entry, bars, cfg)
        assert result.reason == "stop"
        assert result.triggered_at == bars.index[1]
        # No extra slippage on gap — fill at open
        assert result.exit_price == 98.0

    # 4. Hit-both-same-bar: conservative (stop wins)
    def test_hit_both_same_bar_stop_wins(self):
        entry = 100.0
        cfg = StopTakeConfig(stop_loss_pct=0.01, take_profit_pct=0.07)
        # Single bar with low=98.5 (<99.0) AND high=108.0 (>107.0)
        bars = _bars(
            opens=[100.5],
            highs=[108.0],
            lows=[98.5],
            closes=[103.0],
        )
        result = simulate_stop_take(entry, bars, cfg)
        assert result.reason == "stop", "Conservative: stop must win when both hit same bar"
        assert abs(result.exit_price - 99.0 * (1 - 0.0005)) < 1e-9

    # 5. Stop triggered after several healthy bars
    def test_stop_after_several_bars(self):
        entry = 100.0
        cfg = StopTakeConfig(stop_loss_pct=0.01)  # stop at 99.0
        bars = _bars(
            opens=[100.5, 101.0, 102.0, 101.5, 100.8, 99.5],
            highs=[101.0, 102.0, 103.0, 102.5, 101.5, 100.5],
            lows=[100.0, 100.5, 101.5, 100.8, 100.2, 98.9],  # last bar hits
            closes=[100.8, 101.5, 102.5, 101.8, 101.0, 99.2],
        )
        result = simulate_stop_take(entry, bars, cfg)
        assert result.reason == "stop"
        assert result.triggered_at == bars.index[5]

    # 6. signal_exit fires before stop/take
    def test_signal_exit_before_stop(self):
        entry = 100.0
        cfg = StopTakeConfig(stop_loss_pct=0.01, take_profit_pct=0.07)
        bars = _bars(
            opens=[100.5, 101.0, 101.5],
            highs=[101.0, 102.0, 102.5],
            lows=[100.0, 100.5, 101.0],
            closes=[100.8, 101.5, 102.0],
        )
        # Signal exit on bar index[1] — stop/take not hit
        signal_ts = bars.index[1]
        result = simulate_stop_take(entry, bars, cfg, signal_exit_bar=signal_ts)
        assert result.reason == "signal_exit"
        assert result.triggered_at == bars.index[1]
        assert result.exit_price == 101.5  # close of bar 1

    # 7. No exit: position survives all bars
    def test_no_exit_survives_all_bars(self):
        entry = 100.0
        cfg = StopTakeConfig(stop_loss_pct=0.01, take_profit_pct=0.07)
        # Gentle bars: low never < 99.0, high never > 107.0
        bars = _bars(
            opens=[100.5, 101.0, 102.0],
            highs=[101.0, 103.0, 106.0],
            lows=[100.0, 100.5, 101.5],
            closes=[100.8, 102.5, 105.0],
        )
        result = simulate_stop_take(entry, bars, cfg)
        assert result.reason is None
        assert result.triggered_at is None
        assert result.exit_price is None

    # 8. Wider stop (2 % ATR-proxy) survives volatility that 1 % stop catches
    def test_wider_stop_survives_where_narrow_stops(self):
        entry = 100.0
        cfg_narrow = StopTakeConfig(stop_loss_pct=0.01)   # stop at 99.0
        cfg_wide = StopTakeConfig(stop_loss_pct=0.02)     # stop at 98.0
        bars = _bars(
            opens=[100.5, 100.2, 99.5],
            highs=[101.0, 101.0, 100.5],
            lows=[100.0, 99.8, 98.5],   # bar 1 low=99.8 < 99.0 but > 98.0
            closes=[100.8, 100.1, 99.5],
        )
        narrow_result = simulate_stop_take(entry, bars, cfg_narrow)
        wide_result = simulate_stop_take(entry, bars, cfg_wide)
        assert narrow_result.reason == "stop"     # 1% stop caught
        assert wide_result.reason is None         # 2% stop survived

    # 9. Invalid input: empty bars
    def test_empty_bars_raises(self):
        entry = 100.0
        cfg = StopTakeConfig(stop_loss_pct=0.01)
        empty = pd.DataFrame(columns=["open", "high", "low", "close"])
        with pytest.raises(ValueError, match="empty"):
            simulate_stop_take(entry, empty, cfg)

    # 10. Invalid input: missing column
    def test_missing_column_raises(self):
        entry = 100.0
        cfg = StopTakeConfig(stop_loss_pct=0.01)
        bad = pd.DataFrame({"open": [100.0], "high": [101.0], "low": [99.0]})
        with pytest.raises(ValueError, match="missing columns"):
            simulate_stop_take(entry, bad, cfg)
