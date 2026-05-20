"""Live universe-scanner: 20-bar high breakout with ATR-based trailing exit (#227 S4).

Per-symbol entry rule:
    close[-1] >= max(close[-21:-1])   (20-bar high breakout)

Exit policy: this strategy declares a non-null ``trailing_stop_pct`` so the
``LivePositionRiskManager`` lets price ride and only exits on a 4% pullback
from the running peak. ``stop_loss_pct`` and ``take_profit_pct`` are set
generously so the trailing rule dominates intraday exits — see spec md
``docs/specs/strategies/live-breakout-with-atr-stop.md``.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin


class LiveBreakoutWithAtrStop(LiveScannerMixin):
    BREAKOUT_LOOKBACK: ClassVar[int] = 20
    MIN_HISTORY: ClassVar[int] = 30

    # Trailing stop is the primary exit — give stop_loss / take_profit
    # generous bands so they only catch extreme outliers.
    stop_loss_pct: ClassVar[float] = 0.05
    take_profit_pct: ClassVar[float] = 0.20
    trailing_stop_pct: ClassVar[float] = 0.04

    def __init__(
        self, *,
        default_size: float = 0.05,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
    ) -> None:
        if not 0 < default_size <= 1.0:
            raise ValueError(f"default_size must be in (0, 1], got {default_size}")
        self.default_size = default_size
        if stop_loss_pct is not None:
            self.stop_loss_pct = stop_loss_pct
        if take_profit_pct is not None:
            self.take_profit_pct = take_profit_pct
        if trailing_stop_pct is not None:
            self.trailing_stop_pct = trailing_stop_pct

    async def on_bar(self, ctx: object) -> Signal | None:
        snap = ctx["market_snapshot"]  # type: ignore[index]
        history: pd.DataFrame | None = snap.get("history")
        if history is None or len(history) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="warmup")

        close = history["close"]
        baseline = close.iloc[-(self.BREAKOUT_LOOKBACK + 1):-1]
        if len(baseline) < self.BREAKOUT_LOOKBACK:
            return Signal(action="hold", size=0.0, reason="baseline_short")
        prior_max = float(baseline.max())
        last_close = float(close.iloc[-1])
        if last_close < prior_max:
            return Signal(
                action="hold", size=0.0,
                reason=f"no_breakout:last={last_close:.0f},max={prior_max:.0f}",
            )

        return Signal(
            action="buy",
            size=self.default_size,
            reason=(
                f"atr_breakout:last={last_close:.0f},max20={prior_max:.0f},"
                f"trailing_pct={self.trailing_stop_pct:.2%}"
            ),
        )
