"""Live universe-scanner: Airborne BB-reversal v2 — adds trend alignment gate.

v1 (``live_airborne_bb_reversal``) was rejected at PF=0.912 (best, 1y BTC+ETH
1h). v2's pre-registered hypothesis:

    "Adding a trend-alignment gate (current close > SMA(N)) restricts long
    entries to established uptrends, where BB lower-band reclaim is
    statistically more likely to mean-revert (per lecture §3 — '큰 프레임
    추세가 작은 프레임을 이긴다'). Does this lift PF above 1.0?"

Same per-symbol semantics as v1 + one additional gate. If the trend gate makes
no statistical difference (PF stays in 0.85-0.95), the v1 verdict generalizes
to "BB-reversal family is unsalvageable" rather than "v1's entry signal was
the bottleneck".
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

import signals
from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from signals.airborne_bb_reversal import RETRACE_RATIO, evaluate_long_fire


class LiveAirborneBbReversalV2(LiveScannerMixin):
    BB_WINDOW: ClassVar[int] = 20
    BB_STD: ClassVar[float] = 2.0
    MAX_LOOKBACK: ClassVar[int] = 50
    # NEW: trend alignment SMA period (in bars of chart TF).
    # 100 bars on 1h ≈ 4 days. Tunable via __init__.
    TREND_SMA_PERIOD: ClassVar[int] = 100
    MIN_HISTORY: ClassVar[int] = max(BB_WINDOW + 2, TREND_SMA_PERIOD + 1)

    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06

    def __init__(
        self,
        *,
        default_size: float = 0.05,
        trend_sma_period: int | None = None,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
    ) -> None:
        if not 0 < default_size <= 1.0:
            raise ValueError(f"default_size must be in (0, 1], got {default_size}")
        self.default_size = default_size
        if trend_sma_period is not None:
            if trend_sma_period < 2:
                raise ValueError(
                    f"trend_sma_period must be >= 2, got {trend_sma_period}"
                )
            self.trend_sma_period = trend_sma_period
        else:
            self.trend_sma_period = self.TREND_SMA_PERIOD
        # Recompute min_history if instance overrides class attr.
        self.min_history = max(self.BB_WINDOW + 2, self.trend_sma_period + 1)
        if stop_loss_pct is not None:
            self.stop_loss_pct = stop_loss_pct
        if take_profit_pct is not None:
            self.take_profit_pct = take_profit_pct
        if trailing_stop_pct is not None:
            self.trailing_stop_pct = trailing_stop_pct

    async def on_bar(self, ctx: object) -> Signal | None:
        snap = ctx["market_snapshot"]  # type: ignore[index]
        history: pd.DataFrame | None = snap.get("history")
        if history is None or len(history) < self.min_history:
            return Signal(action="hold", size=0.0, reason="warmup")

        close = history["close"]
        bb = signals.compute(
            "bollinger", close=close, window=self.BB_WINDOW, n_std=self.BB_STD,
        )
        lower = bb["lower"]
        if pd.isna(lower.iloc[-1]) or pd.isna(lower.iloc[-2]):
            return Signal(action="hold", size=0.0, reason="bb_warmup")

        # NEW: trend gate — close[-1] must be above SMA(trend_sma_period).
        sma_trend = close.rolling(self.trend_sma_period).mean()
        if pd.isna(sma_trend.iloc[-1]):
            return Signal(action="hold", size=0.0, reason="trend_warmup")
        c_now = float(close.iloc[-1])
        trend = float(sma_trend.iloc[-1])
        if c_now <= trend:
            return Signal(
                action="hold", size=0.0,
                reason=f"trend_gate:c={c_now:.4f}<=sma{self.trend_sma_period}={trend:.4f}",
            )

        fires, setup, trigger = evaluate_long_fire(
            history=history,
            bb_lower=lower,
            max_lookback=self.MAX_LOOKBACK,
        )

        if setup is None:
            return Signal(action="hold", size=0.0, reason="no_active_setup")

        bars_since = len(history) - 1 - setup.breakout_index

        if not fires:
            return Signal(
                action="hold", size=0.0,
                reason=(
                    f"airborne_v2_pending:bo@-{bars_since},"
                    f"base={setup.base:.4f},ext={setup.extreme:.4f},"
                    f"trig={trigger:.4f},c={c_now:.4f},trend_ok"
                ),
            )

        return Signal(
            action="buy",
            size=self.default_size,
            reason=(
                f"airborne_v2_fire:bo@-{bars_since},"
                f"base={setup.base:.4f},ext={setup.extreme:.4f},"
                f"trig={trigger:.4f},c={c_now:.4f}>sma{self.trend_sma_period}={trend:.4f},"
                f"ratio={RETRACE_RATIO}"
            ),
        )
