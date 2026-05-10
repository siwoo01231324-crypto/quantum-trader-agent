"""Live universe-scanner: Bullish RSI divergence inside a downtrend (#227 S4).

Per-symbol entry rule:
    close[-1] < close[-22]                  (downtrend over last 21 bars)
    AND detect_divergence(close, rsi, 14)[-1] == 'bullish'
                                            (price made a new low but RSI did not)

This is the universe-wide variant of ``momo_kis_v1``'s entry rule, lifted out
of the single-ticker (005930) constraint. The downtrend filter prevents the
divergence rule from firing on choppy sideways action.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin


class LiveOversoldWithDivergence(LiveScannerMixin):
    required_factors: ClassVar[list[str]] = ["rsi"]
    DIVERGENCE_LOOKBACK: ClassVar[int] = 14
    DOWNTREND_LOOKBACK: ClassVar[int] = 21
    # detect_divergence shifts price/RSI by 1 + rolling(lookback) + shift(lookback)
    # so the function needs at least ~2 * lookback + 2 valid bars to emit non-NaN.
    MIN_HISTORY: ClassVar[int] = 60

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
        if len(close) <= self.DOWNTREND_LOOKBACK:
            return Signal(action="hold", size=0.0, reason="downtrend_warmup")
        c_now = float(close.iloc[-1])
        c_past = float(close.iloc[-(self.DOWNTREND_LOOKBACK + 1)])
        if c_now >= c_past:
            return Signal(
                action="hold", size=0.0,
                reason=f"not_downtrending:now={c_now:.0f},past_{self.DOWNTREND_LOOKBACK}={c_past:.0f}",
            )

        factors = ctx.get("factors", {}) if isinstance(ctx, dict) else {}  # type: ignore[union-attr]
        rsi: pd.Series | None = factors.get("rsi") if isinstance(factors, dict) else None
        if rsi is None or len(rsi) == 0:
            return Signal(action="hold", size=0.0, reason="rsi_missing")

        from signals.rsi import detect_divergence
        div = detect_divergence(close, rsi, self.DIVERGENCE_LOOKBACK)
        latest = div.iloc[-1] if len(div) > 0 else None
        if latest != "bullish":
            return Signal(
                action="hold", size=0.0,
                reason=f"no_bullish_divergence:latest={latest}",
            )

        return Signal(
            action="buy",
            size=self.default_size,
            reason=(
                f"oversold_divergence:c_now={c_now:.0f}<c_past={c_past:.0f},"
                f"div=bullish"
            ),
        )
