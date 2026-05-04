"""Intra-bar stop-loss / take-profit simulator.

Issue #147: VWMA + stop-loss/take-profit 통합 backtest.

Design decisions
----------------
* Conservative same-bar tie-breaking: when both stop and take are hit within
  the same bar the stop is chosen (worst-case / anti-overfit assumption).
* Gap handling: if bar open is already beyond the stop level the position exits
  at open price without additional slippage (gap absorbs full loss).
* Slippage: SLIPPAGE_PCT (0.05 %) applied on top of the trigger price for
  stop and take exits; not applied for signal_exit (engine handles that).
* Long-only: current implementation assumes long positions only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

SLIPPAGE_PCT: float = 0.0005  # 5 bps


@dataclass(frozen=True)
class StopTakeConfig:
    """Parameters for stop-loss / take-profit simulation.

    Parameters
    ----------
    stop_loss_pct:
        Fractional stop-loss distance from entry (e.g. 0.01 = 1 %).
        Must be in (0, 1). ``None`` disables stop-loss.
    take_profit_pct:
        Fractional take-profit distance from entry (e.g. 0.07 = 7 %).
        Must be positive. ``None`` disables take-profit.
    slippage_pct:
        Extra slippage applied to stop/take fill prices (default 0.05 %).
    """

    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    slippage_pct: float = SLIPPAGE_PCT

    def __post_init__(self) -> None:
        if self.stop_loss_pct is not None:
            if not (0.0 < self.stop_loss_pct < 1.0):
                raise ValueError(
                    f"stop_loss_pct must be in (0, 1), got {self.stop_loss_pct}"
                )
        if self.take_profit_pct is not None:
            if self.take_profit_pct <= 0.0:
                raise ValueError(
                    f"take_profit_pct must be > 0, got {self.take_profit_pct}"
                )
        if self.slippage_pct < 0.0:
            raise ValueError(
                f"slippage_pct must be >= 0, got {self.slippage_pct}"
            )


@dataclass(frozen=True)
class StopTakeResult:
    """Result of a single intra-bar stop/take evaluation.

    Attributes
    ----------
    triggered_at:
        Timestamp of the bar where the exit triggered, or ``None`` if neither
        stop nor take fired on the evaluated bars.
    exit_price:
        Actual fill price (entry-price adjusted for trigger + slippage), or
        ``None`` when no trigger.
    reason:
        ``"stop"`` | ``"take"`` | ``"signal_exit"`` | ``None``
    """

    triggered_at: pd.Timestamp | None
    exit_price: float | None
    reason: Literal["stop", "take", "signal_exit"] | None


def simulate_stop_take(
    entry_price: float,
    bars: pd.DataFrame,
    config: StopTakeConfig,
    *,
    signal_exit_bar: pd.Timestamp | None = None,
) -> StopTakeResult:
    """Evaluate stop-loss / take-profit for a long position bar by bar.

    The function walks ``bars`` in chronological order and returns as soon as
    the first exit condition fires.  If ``signal_exit_bar`` is reached before
    any stop/take triggers the exit reason is ``"signal_exit"``.

    Parameters
    ----------
    entry_price:
        Fill price of the entry (post-slippage from the engine).
    bars:
        OHLCV DataFrame with at minimum columns ``open``, ``high``, ``low``,
        ``close``.  Index must be monotonically increasing timestamps.
    config:
        Stop/take parameters.
    signal_exit_bar:
        Optional timestamp at which a strategy-driven exit signal fires.
        Evaluated *after* stop/take on the same bar (stop/take takes
        priority on the same bar).

    Returns
    -------
    StopTakeResult
        The first exit that fires, or a result with all ``None`` fields if
        the position survives all bars without any exit.

    Raises
    ------
    ValueError
        If ``bars`` is empty or missing required columns.
    """
    required_cols = {"open", "high", "low", "close"}
    missing = required_cols - set(bars.columns)
    if missing:
        raise ValueError(f"bars DataFrame missing columns: {missing}")
    if bars.empty:
        raise ValueError("bars DataFrame is empty")
    if entry_price <= 0:
        raise ValueError(f"entry_price must be > 0, got {entry_price}")

    stop_level: float | None = None
    take_level: float | None = None

    if config.stop_loss_pct is not None:
        stop_level = entry_price * (1.0 - config.stop_loss_pct)
    if config.take_profit_pct is not None:
        take_level = entry_price * (1.0 + config.take_profit_pct)

    slip = config.slippage_pct

    for ts, row in bars.iterrows():
        bar_open: float = float(row["open"])
        bar_high: float = float(row["high"])
        bar_low: float = float(row["low"])

        # --- gap-down check (open already below stop) ---
        if stop_level is not None and bar_open <= stop_level:
            return StopTakeResult(
                triggered_at=ts,
                exit_price=bar_open,  # no extra slippage on gap
                reason="stop",
            )

        # --- gap-up check (open already above take) ---
        if take_level is not None and bar_open >= take_level:
            return StopTakeResult(
                triggered_at=ts,
                exit_price=bar_open,  # fill at open, no additional slippage
                reason="take",
            )

        # --- intra-bar: both levels potentially hit ---
        stop_hit = stop_level is not None and bar_low <= stop_level
        take_hit = take_level is not None and bar_high >= take_level

        if stop_hit and take_hit:
            # Conservative: stop wins (worst-case assumption)
            exit_px = stop_level * (1.0 - slip)
            return StopTakeResult(triggered_at=ts, exit_price=exit_px, reason="stop")

        if stop_hit:
            exit_px = stop_level * (1.0 - slip)
            return StopTakeResult(triggered_at=ts, exit_price=exit_px, reason="stop")

        if take_hit:
            exit_px = take_level * (1.0 + slip)
            return StopTakeResult(triggered_at=ts, exit_price=exit_px, reason="take")

        # --- signal exit check (evaluated after stop/take on same bar) ---
        if signal_exit_bar is not None and ts >= signal_exit_bar:
            close_px = float(row["close"])
            return StopTakeResult(
                triggered_at=ts, exit_price=close_px, reason="signal_exit"
            )

    return StopTakeResult(triggered_at=None, exit_price=None, reason=None)
