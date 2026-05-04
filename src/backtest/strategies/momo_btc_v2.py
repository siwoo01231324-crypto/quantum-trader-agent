from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal, Optional

import pandas as pd

from backtest.protocol import Bar, Signal, Strategy
from risk.sizing import consensus_kelly, ewma_sigma, fractional_kelly, kelly_continuous, vol_target
from signals.rsi import detect_divergence

if TYPE_CHECKING:
    from ml.meta_labeler import MetaLabeler

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

    #85 META-LABELING (opt-in, default disabled):
      `metalabeler` — When not None, a fitted MetaLabeler is called on each
        bullish divergence signal. If win_probability < metalabeler_threshold,
        the signal is rejected (action="hold", reason="metalabeler_reject").
        metalabeler=None (default) preserves bit-identical bypass behavior.
    """

    required_factors: ClassVar[list[str]] = ["rsi"]
    RSI_PERIOD: int = 14
    LOOKBACK: int = 14

    def __init__(
        self,
        *,
        symbol: str = "BTCUSDT",              # cross-asset gate (#177)
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
        metalabeler: Optional["MetaLabeler"] = None,
        metalabeler_threshold: float = 0.5,
    ) -> None:
        if sizing_lookback < 2:
            raise ValueError(f"sizing_lookback must be >= 2, got {sizing_lookback}")
        self.symbol = symbol
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
        self._metalabeler = metalabeler
        self._metalabeler_threshold = metalabeler_threshold

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

    def _extract_metalabeler_features(
        self,
        bar: Bar,
        history: pd.DataFrame,
        context: dict,
        div_magnitude: float,
        atr_val: float,
        bars_since_pivot: int,
        conf: float,
    ) -> pd.DataFrame:
        """Build a 1-row feature DataFrame for the MetaLabeler from current bar state."""
        rsi_series = context.get("factors", {}).get("rsi", pd.Series(dtype=float))
        rsi_val = float(rsi_series.iloc[-1]) if len(rsi_series) > 0 else 0.0
        return pd.DataFrame([{
            "rsi": rsi_val,
            "atr": atr_val,
            "divergence_magnitude": div_magnitude,
            "bars_since_pivot": bars_since_pivot,
            "confidence": conf,
            "close": bar.close,
            "volume": bar.volume,
        }])

    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
        # Cross-asset gate (#177). _StrategyAdapter injects the live snapshot's
        # symbol into context so this BTC-tuned strategy stays out of KRX
        # tickers when both are registered in the same orchestrator. Treats
        # missing context["symbol"] as opt-in (legacy unit tests / engine).
        ctx_symbol = context.get("symbol")
        if ctx_symbol is not None and ctx_symbol != self.symbol:
            return Signal(action="hold", size=0.0, reason="symbol_mismatch")

        min_bars = self.RSI_PERIOD + self.LOOKBACK * 2 + 1
        if len(history) < min_bars:
            return Signal(action="hold", size=0.0, reason="warmup")

        close = history["close"]
        rsi = context.get("factors", {}).get("rsi", pd.Series(dtype=float))
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

            # --- MetaLabeler hook (bypass when metalabeler=None) ---
            if self._metalabeler is not None:
                feat = self._extract_metalabeler_features(
                    bar, history, context, div_magnitude, atr_val, bars_since_pivot, conf
                )
                p_take = float(self._metalabeler.win_probability(feat)[0])
                if p_take < self._metalabeler_threshold:
                    return Signal(
                        action="hold",
                        size=0.0,
                        reason="metalabeler_reject",
                        win_probability=p_take,
                    )
                return Signal(
                    action="buy",
                    size=size,
                    reason="bullish divergence",
                    expected_return=mu_hat,
                    confidence=conf,
                    win_probability=p_take,
                )
            # -------------------------------------------------------

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
