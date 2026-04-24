"""BTCUSDT 4h volatility-filtered momentum strategy."""
from __future__ import annotations

import math
from typing import ClassVar

import numpy as np
import pandas as pd

from backtest.protocol import AsyncStrategy, Signal
from risk.sizing import ewma_sigma, vol_target
import signals


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


class MomoVolFiltered:
    """BTCUSDT 4h MACD momentum gated by realized volatility.

    Tunable parameters (param grid <= 3 axes):
      - vol_ceiling: annualized realized vol threshold for entry gate (default 0.80)
      - vol_target_annual: annualized vol target for position sizing (default 0.20)
      - macd_slow: MACD slow EMA period (default 26); fast=12, signal=9 fixed

    Entry: MACD histogram > 0 AND MACD line > signal line AND realized_vol < vol_ceiling.
    Exit: MACD histogram < 0 (normal). realized_vol > vol_ceiling * 1.5 (emergency).
    Bar boundary: every 4h (UTC hours 0,4,8,12,16,20).
    """

    required_factors: ClassVar[list[str]] = ["macd", "realized_vol", "atr"]

    SYMBOL = "BTCUSDT"
    MIN_HISTORY = 27  # max(MACD slow=26, vol window=20) + 1

    def __init__(
        self,
        *,
        vol_ceiling: float = 0.80,
        vol_target_annual: float = 0.20,
        macd_slow: int = 26,
    ) -> None:
        self.vol_ceiling = vol_ceiling
        self.vol_target_annual = vol_target_annual
        self.macd_slow = macd_slow
        self._periods_per_year = 365 * 6  # 4h bars/year

    def _is_my_bar_boundary(self, ts: pd.Timestamp) -> bool:
        return ts.hour % 4 == 0 and ts.minute == 0 and ts.second == 0

    async def on_bar(self, ctx: object) -> Signal | None:
        ts = ctx["ts"]
        if not self._is_my_bar_boundary(ts):
            return Signal(action="hold", size=0.0, reason="not my bar")

        snap = ctx["market_snapshot"]
        hist = snap.get("ohlcv_history", {}).get(self.SYMBOL)
        if hist is None or len(hist) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="insufficient history")

        close = hist["close"]
        high = hist["high"]
        low = hist["low"]

        macd_df = signals.compute("macd", close=close, slow=self.macd_slow)
        vol_series = signals.compute(
            "realized_vol", close=close, window=20, annualize=self._periods_per_year
        )
        atr_series = signals.compute("atr", high=high, low=low, close=close)

        macd_hist_val = float(macd_df["histogram"].iloc[-1])
        macd_line_val = float(macd_df["macd"].iloc[-1])
        signal_line_val = float(macd_df["signal"].iloc[-1])
        vol_val = float(vol_series.iloc[-1])
        atr_val = float(atr_series.iloc[-1]) if not np.isnan(atr_series.iloc[-1]) else 1.0

        # Emergency exit: extreme volatility regardless of MACD
        if vol_val > self.vol_ceiling * 1.5:
            return Signal(action="sell", size=1.0, reason="emergency: vol spike")

        # Normal exit: MACD histogram negative
        if macd_hist_val < 0:
            return Signal(action="sell", size=1.0, reason="MACD histogram negative")

        # Entry: MACD momentum + vol filter
        if macd_hist_val > 0 and macd_line_val > signal_line_val and vol_val < self.vol_ceiling:
            returns = close.pct_change().dropna()
            if len(returns) < 2:
                return Signal(action="hold", size=0.0, reason="insufficient returns")

            sigma = ewma_sigma(returns, lam=0.94)
            size = vol_target(
                sigma_period=sigma,
                target_annual=self.vol_target_annual,
                periods_per_year=self._periods_per_year,
            )

            confidence = _clip01(abs(macd_hist_val) / atr_val) if atr_val > 0 else 0.0
            expected_return = float(returns.iloc[-60:].mean()) if len(returns) >= 60 else float(returns.mean())

            return Signal(
                action="buy",
                size=size,
                reason="MACD momentum + vol filter",
                confidence=confidence,
                expected_return=expected_return,
            )

        return Signal(action="hold", size=0.0, reason="no signal")
