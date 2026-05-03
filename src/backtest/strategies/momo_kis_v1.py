"""KIS KRX 15m momentum strategy (RSI divergence entry, single stock 005930)."""
from __future__ import annotations

from datetime import time
from typing import ClassVar

import pandas as pd

from backtest.protocol import AsyncStrategy, Signal
from risk.sizing import ewma_sigma, fractional_kelly, kelly_continuous
from signals.rsi import detect_divergence
from universe.krx_calendar import KST, is_krx_holiday


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


class MomoKisV1:
    """KIS KRX 15m momentum strategy using RSI divergence.

    Entry: bullish RSI divergence → buy with half-kelly sizing.
    Exit: bearish RSI divergence → sell all (size=1.0).
    Bar boundary: KST 09:00~15:30, 15-minute intervals, weekday, non-holiday.

    Double safety guard: orchestrator harness gate + self._is_my_bar_boundary.
    """

    required_factors: ClassVar[list[str]] = ["rsi"]

    SYMBOL_DEFAULT = "005930"
    RSI_PERIOD = 14
    LOOKBACK = 14

    def __init__(
        self,
        *,
        symbol: str = "005930",
        sizing_mode: str = "half-kelly",
        sizing_lookback: int = 60,
        kelly_k: float = 0.5,
        target_annual: float = 0.15,
        periods_per_year: int = 26 * 252,
        ewma_lam: float = 0.94,
        interval_min: int = 15,
    ) -> None:
        if sizing_lookback < 2:
            raise ValueError(f"sizing_lookback must be >= 2, got {sizing_lookback}")
        self.symbol = symbol
        self.sizing_mode = sizing_mode
        self.sizing_lookback = sizing_lookback
        self.kelly_k = kelly_k
        self.target_annual = target_annual
        self.periods_per_year = periods_per_year
        self.ewma_lam = ewma_lam
        self.interval_min = interval_min

    def _is_my_bar_boundary(self, ts: pd.Timestamp) -> bool:
        """Return True only for valid KRX 15m bar boundaries in KST."""
        if ts.tzinfo is not None:
            ts_kst = ts.astimezone(KST)
        else:
            ts_kst = ts
        if ts_kst.weekday() >= 5:
            return False
        if is_krx_holiday(ts_kst.date()):
            return False
        t = ts_kst.time()
        if not (time(9, 0) <= t <= time(15, 30)):
            return False
        return (t.minute % self.interval_min == 0) and t.second == 0

    def _entry_size(self, close: pd.Series) -> float:
        window = close.iloc[-(self.sizing_lookback + 1):]
        returns = window.pct_change().dropna()
        if len(returns) < 2:
            return 0.0
        sigma = ewma_sigma(returns, lam=self.ewma_lam)
        if sigma <= 1e-9:
            return 0.0
        mu = float(returns.mean())
        full = kelly_continuous(mu=mu, sigma=sigma)
        return _clip01(fractional_kelly(full, k=self.kelly_k))

    async def on_bar(self, ctx: object) -> Signal | None:
        ts = ctx["ts"]
        if not self._is_my_bar_boundary(ts):
            return Signal(action="hold", size=0.0, reason="not my bar")

        snap = ctx["market_snapshot"]
        history: pd.DataFrame | None = snap.get("history")
        rsi: pd.Series = ctx.get("factors", {}).get("rsi", pd.Series(dtype=float))

        min_bars = self.RSI_PERIOD + self.LOOKBACK * 2 + 1
        if history is None or len(history) < min_bars:
            return Signal(action="hold", size=0.0, reason="warmup")

        close = history["close"]
        div = detect_divergence(close, rsi, self.LOOKBACK)
        latest = div.iloc[-1]

        if latest == "bullish":
            size = self._entry_size(close)
            if size <= 0.0:
                return Signal(action="hold", size=0.0, reason="bullish divergence (sized=0)")
            window = close.iloc[-(self.sizing_lookback + 1):]
            returns = window.pct_change().dropna()
            mu_hat = float(returns.mean()) if len(returns) >= 2 else 0.0
            return Signal(
                action="buy",
                size=size,
                reason="bullish divergence",
                expected_return=mu_hat,
            )

        if latest == "bearish":
            return Signal(action="sell", size=1.0, reason="bearish divergence")

        return Signal(action="hold", size=0.0, reason="no signal")
