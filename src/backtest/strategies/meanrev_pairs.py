"""ETHBTC 1h cross-pair mean-reversion strategy."""
from __future__ import annotations

from typing import ClassVar

import numpy as np
import pandas as pd

from backtest.protocol import AsyncStrategy, Signal
from risk.sizing import ewma_sigma, vol_target
import signals


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


class MeanrevPairs:
    """ETHBTC 1h log-price mean-reversion via rolling z-score.

    Entry: z < -entry_threshold -> buy (ratio fallen below mean, expect recovery).
    Exit:  z > 0 -> sell (conservative: unwind long when ratio crosses above mean).
    Bar boundary: every 1h (:00 UTC).
    """

    SYMBOL = "ETHBTC"
    MIN_HISTORY = 61  # window=60 + 1

    def __init__(
        self,
        *,
        entry_threshold: float = 2.0,
        zscore_window: int = 60,
        vol_target_annual: float = 0.15,
    ) -> None:
        self.entry_threshold = entry_threshold
        self.zscore_window = zscore_window
        self.vol_target_annual = vol_target_annual
        self._periods_per_year = 365 * 24  # 1h bars/year

    def _is_my_bar_boundary(self, ts: pd.Timestamp) -> bool:
        return ts.minute == 0 and ts.second == 0

    def _confidence(self, z: float) -> float:
        return _clip01(1.0 - abs(z) / 4.0)

    async def on_bar(self, ctx: object) -> Signal | None:
        ts = ctx["ts"]
        if not self._is_my_bar_boundary(ts):
            return Signal(action="hold", size=0.0, reason="not my bar")

        snap = ctx["market_snapshot"]
        hist = snap.get("ohlcv_history", {}).get(self.SYMBOL)
        if hist is None or len(hist) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="insufficient history")

        close = hist["close"]

        z_series = signals.compute("zscore", close=close, window=self.zscore_window)
        z = float(z_series.iloc[-1])

        if np.isnan(z):
            return Signal(action="hold", size=0.0, reason="zscore NaN (warmup)")

        returns = close.pct_change().dropna()
        expected_return = float(returns.iloc[-60:].mean()) if len(returns) >= 60 else float(returns.mean())

        # Exit: z crossed above 0
        if z > 0:
            return Signal(
                action="sell",
                size=1.0,
                reason=f"z={z:.2f} above 0 (exit long)",
                confidence=self._confidence(z),
                expected_return=expected_return,
            )

        # Entry: z deeply negative
        if z < -self.entry_threshold:
            if len(returns) < 2:
                return Signal(action="hold", size=0.0, reason="insufficient returns")

            sigma = ewma_sigma(returns, lam=0.94)
            size = vol_target(
                sigma_period=sigma,
                target_annual=self.vol_target_annual,
                periods_per_year=self._periods_per_year,
            )
            return Signal(
                action="buy",
                size=size,
                reason=f"z={z:.2f} below -{self.entry_threshold} (mean-reversion entry)",
                confidence=self._confidence(z),
                expected_return=expected_return,
            )

        return Signal(action="hold", size=0.0, reason=f"z={z:.2f} within band")
