from __future__ import annotations

import math
from typing import Literal

import pandas as pd

from backtest.protocol import Bar, Signal, Strategy
from risk.sizing import consensus_kelly, ewma_sigma, fractional_kelly, kelly_continuous, vol_target
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

    #87 PATENT-DERIVED OPTIONS (all opt-in, default disabled):
      `use_consensus_kelly` — When True and `sizing_mode="half-kelly"`, the k
        multiplier is linearly scaled between `consensus_k_base` (0.0 agreement)
        and `consensus_k_max` (1.0 agreement). Caller supplies `signal_agreement`
        ∈ [0, 1] representing indicator alignment. See risk.sizing.consensus_kelly
        and tests/test_consensus_kelly.py. Default False → fractional_kelly(full, kelly_k).

    See docs/specs/risk-rule-dsl.md §8.1 for the full catalog of #87 options,
    including DSL-level extensions (cvar_levels, extreme_fear_block).
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
        use_consensus_kelly: bool = False,
        signal_agreement: float = 0.0,
        consensus_k_base: float = 0.5,
        consensus_k_max: float = 0.75,
    ) -> None:
        if sizing_lookback < 2:
            raise ValueError(f"sizing_lookback must be >= 2, got {sizing_lookback}")
        self.sizing_mode: SizingMode = sizing_mode
        self.sizing_lookback = sizing_lookback
        self.kelly_k = kelly_k
        self.target_annual = target_annual
        self.periods_per_year = periods_per_year
        self.ewma_lam = ewma_lam
        self.use_consensus_kelly = use_consensus_kelly
        self.signal_agreement = signal_agreement
        self.consensus_k_base = consensus_k_base
        self.consensus_k_max = consensus_k_max

    def on_init(self, context: dict) -> None:
        pass

    def _compute_confidence(
        self,
        div_magnitude: float,
        atr: float,
        bars_since_pivot: int,
    ) -> float:
        """Confidence score for a bullish divergence signal.

        Formula: clip01(|div_magnitude| / atr * min(bars_since_pivot / LOOKBACK, 1))
        """
        if atr <= 0.0:
            return 0.0
        return max(0.0, min(1.0, abs(div_magnitude) / atr * min(bars_since_pivot / self.LOOKBACK, 1.0)))

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
            if self.use_consensus_kelly:
                return consensus_kelly(
                    full,
                    self.signal_agreement,
                    k_base=self.consensus_k_base,
                    k_max=self.consensus_k_max,
                )
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

            # Compute μ̂ from recent returns
            window = close.iloc[-(self.sizing_lookback + 1):]
            returns = window.pct_change().dropna()
            mu_hat = float(returns.mean()) if len(returns) >= 2 else 0.0

            # Compute confidence from divergence magnitude, ATR, bars since pivot
            from signals.atr import compute_atr
            atr_series = compute_atr(history["high"], history["low"], close)
            atr_val = float(atr_series.iloc[-1]) if not atr_series.empty else 0.0
            div_magnitude = float(close.iloc[-1] - close.iloc[-self.LOOKBACK - 1])
            # bars_since_pivot: count bars since last non-bullish in window
            recent_div = div.iloc[-self.LOOKBACK:]
            bullish_indices = [i for i, v in enumerate(recent_div) if v == "bullish"]
            bars_since_pivot = (len(recent_div) - bullish_indices[0]) if bullish_indices else self.LOOKBACK
            conf = self._compute_confidence(div_magnitude, atr_val, bars_since_pivot)

            return Signal(
                action="buy",
                size=size,
                reason="bullish divergence",
                expected_return=mu_hat,
                confidence=conf,
            )
        elif latest == "bearish":
            return Signal(action="sell", size=1.0, reason="bearish divergence")
        return Signal(action="hold", size=0.0, reason="no signal")
