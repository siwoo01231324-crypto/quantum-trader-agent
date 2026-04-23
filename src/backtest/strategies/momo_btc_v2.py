from __future__ import annotations

from typing import Literal

import pandas as pd

from backtest.protocol import Bar, Signal, Strategy
from risk.sizing import ewma_sigma, fractional_kelly, kelly_continuous, vol_target
from signals.rsi import compute_rsi, detect_divergence

SizingMode = Literal["full", "half-kelly", "vol-target"]


class MomoBtcV2:
    """BTC 15m Momentum v2 (MVP: long-only).

    Bullish divergence -> buy. Bearish divergence -> exit to cash.

    Entry size is controlled by `sizing_mode`:
      - "full": all-in (size=1.0). Backward-compatible default.
      - "half-kelly": kelly_continuous(mu, sigma) * 0.5 over last `sizing_lookback` bars.
      - "vol-target": vol_target(sigma, target_annual, periods_per_year).

    Sell signals always return size=1.0 (exit the whole position).

    Sizing math lives in `risk.sizing` (pure functions, no LLM) and is clamped
    to [0, 1] there; final policy-level clamps are the risk DSL's job.
    """

    RSI_PERIOD: int = 14
    LOOKBACK: int = 14

    def __init__(
        self,
        *,
        sizing_mode: SizingMode = "full",
        sizing_lookback: int = 60,
        kelly_k: float = 0.5,
        target_annual: float = 0.20,          # crypto default, see docs/background/20-position-sizing.md §8
        periods_per_year: int = 365 * 96,     # 15m bars per year for BTC perp (no session breaks)
        ewma_lam: float = 0.94,               # RiskMetrics 1996
    ) -> None:
        if sizing_lookback < 2:
            raise ValueError(f"sizing_lookback must be >= 2, got {sizing_lookback}")
        self.sizing_mode: SizingMode = sizing_mode
        self.sizing_lookback = sizing_lookback
        self.kelly_k = kelly_k
        self.target_annual = target_annual
        self.periods_per_year = periods_per_year
        self.ewma_lam = ewma_lam

    def on_init(self, context: dict) -> None:
        pass

    def _entry_size(self, close: pd.Series) -> float:
        if self.sizing_mode == "full":
            return 1.0

        window = close.iloc[-(self.sizing_lookback + 1):]
        returns = window.pct_change().dropna()
        if len(returns) < 2:
            return 0.0

        sigma = ewma_sigma(returns, lam=self.ewma_lam)

        if self.sizing_mode == "half-kelly":
            mu = float(returns.mean())
            full = kelly_continuous(mu=mu, sigma=sigma)
            return fractional_kelly(full, k=self.kelly_k)

        if self.sizing_mode == "vol-target":
            return vol_target(
                sigma_period=sigma,
                target_annual=self.target_annual,
                periods_per_year=self.periods_per_year,
            )

        raise ValueError(f"unknown sizing_mode: {self.sizing_mode!r}")

    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
        min_bars = self.RSI_PERIOD + self.LOOKBACK * 2 + 1
        if len(history) < min_bars:
            return Signal(action="hold", size=0.0, reason="warmup")

        close = history["close"]
        rsi = compute_rsi(close, self.RSI_PERIOD)
        div = detect_divergence(close, rsi, self.LOOKBACK)

        latest = div.iloc[-1]
        if latest == "bullish":
            size = self._entry_size(close)
            if size <= 0.0:
                return Signal(action="hold", size=0.0, reason="bullish divergence (sized=0)")
            return Signal(action="buy", size=size, reason="bullish divergence")
        elif latest == "bearish":
            return Signal(action="sell", size=1.0, reason="bearish divergence")
        return Signal(action="hold", size=0.0, reason="no signal")
