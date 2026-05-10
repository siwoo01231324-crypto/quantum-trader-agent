"""Live universe-scanner: MACD bullish cross + 20-bar high breakout (#227 S4).

Per-symbol entry rule:
    histogram[-2] <= 0 AND histogram[-1] > 0    (MACD bullish cross)
    AND close[-1] >= max(close[-21:-1])         (20-bar high breakout)

Both conditions must fire on the same bar — the cross alone (false positives
mid-trend) and the breakout alone (no momentum confirmation) each fail too
often in intraday data, so we require their conjunction. Exit is delegated
to ``LivePositionRiskManager``.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

import signals
from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin


class LiveMacdBullishCrossBreakout(LiveScannerMixin):
    BREAKOUT_LOOKBACK: ClassVar[int] = 20
    MIN_HISTORY: ClassVar[int] = 60  # MACD slow=26 + safety margin

    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06

    def __init__(self, *, default_size: float = 0.05) -> None:
        if not 0 < default_size <= 1.0:
            raise ValueError(f"default_size must be in (0, 1], got {default_size}")
        self.default_size = default_size

    async def on_bar(self, ctx: object) -> Signal | None:
        snap = ctx["market_snapshot"]  # type: ignore[index]
        history: pd.DataFrame | None = snap.get("history")
        if history is None or len(history) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="warmup")

        close = history["close"]
        macd_df = signals.compute("macd", close=close)
        hist = macd_df["histogram"]
        if len(hist) < 2 or pd.isna(hist.iloc[-1]) or pd.isna(hist.iloc[-2]):
            return Signal(action="hold", size=0.0, reason="macd_warmup")
        h_prev = float(hist.iloc[-2])
        h_now = float(hist.iloc[-1])
        if not (h_prev <= 0.0 and h_now > 0.0):
            return Signal(
                action="hold", size=0.0,
                reason=f"no_macd_cross:prev={h_prev:.4f},now={h_now:.4f}",
            )

        baseline = close.iloc[-(self.BREAKOUT_LOOKBACK + 1):-1]
        if len(baseline) < self.BREAKOUT_LOOKBACK:
            return Signal(action="hold", size=0.0, reason="breakout_baseline_short")
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
                f"macd_cross_breakout:"
                f"hist_prev={h_prev:.4f},hist_now={h_now:.4f},"
                f"last={last_close:.0f},max20={prior_max:.0f}"
            ),
        )
