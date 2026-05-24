"""Live universe-scanner: Airborne BB-reversal v3 — adds volume gate to v2.

v3 hypothesis (derived from observation that live 에어본(체험판) on a strongly-
trending chart issues far fewer signals than v1/v2 sims, despite identical BB
math): the original indicator gates its fire on more than trend + retracement.
The lecture (§1.4) explicitly says "이탈 직후 진입 금지, 반전 캔들이
**거래량과 함께** 축소될 때까지 대기".

v3 = v2 (trend gate) + volume gate (volume[-1] > SMA(volume, vol_window)),
mirroring the sibling ``live_bb_lower_bounce`` 's volume-confirm pattern.

Goal: see whether layering the volume gate on top of v2's trend gate brings
signal frequency / pattern visibly closer to the original on a live chart, AND
whether 1y PF improves further (or degrades) vs v2's 1.296 ceiling.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

import signals
from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from signals.airborne_bb_reversal import RETRACE_RATIO, evaluate_long_fire


class LiveAirborneBbReversalV3(LiveScannerMixin):
    BB_WINDOW: ClassVar[int] = 20
    BB_STD: ClassVar[float] = 2.0
    MAX_LOOKBACK: ClassVar[int] = 50
    TREND_SMA_PERIOD: ClassVar[int] = 50  # v2's winning value (PF=1.296 @ 50)
    VOLUME_WINDOW: ClassVar[int] = 20
    VOLUME_RATIO_MIN: ClassVar[float] = 1.0  # volume[-1] >= 1.0 * MA
    MIN_HISTORY: ClassVar[int] = max(BB_WINDOW + 2, TREND_SMA_PERIOD + 1, VOLUME_WINDOW + 1)

    stop_loss_pct: ClassVar[float] = 0.02
    take_profit_pct: ClassVar[float] = 0.04

    def __init__(
        self,
        *,
        default_size: float = 0.05,
        trend_sma_period: int | None = None,
        volume_window: int | None = None,
        volume_ratio_min: float | None = None,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
    ) -> None:
        if not 0 < default_size <= 1.0:
            raise ValueError(f"default_size must be in (0, 1], got {default_size}")
        self.default_size = default_size
        self.trend_sma_period = (
            trend_sma_period if trend_sma_period is not None else self.TREND_SMA_PERIOD
        )
        if self.trend_sma_period < 2:
            raise ValueError(f"trend_sma_period >= 2 required, got {self.trend_sma_period}")
        self.volume_window = (
            volume_window if volume_window is not None else self.VOLUME_WINDOW
        )
        if self.volume_window < 2:
            raise ValueError(f"volume_window >= 2 required, got {self.volume_window}")
        self.volume_ratio_min = (
            volume_ratio_min if volume_ratio_min is not None else self.VOLUME_RATIO_MIN
        )
        if self.volume_ratio_min < 0:
            raise ValueError(f"volume_ratio_min >= 0 required, got {self.volume_ratio_min}")
        self.min_history = max(
            self.BB_WINDOW + 2, self.trend_sma_period + 1, self.volume_window + 1,
        )
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

        # Gate (v2): trend alignment.
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

        # Gate (v3 NEW): volume confirmation.
        volume = history["volume"]
        v_base = volume.iloc[-(self.volume_window + 1):-1]
        if len(v_base) < self.volume_window:
            return Signal(action="hold", size=0.0, reason="volume_warmup")
        v_ma = float(v_base.mean())
        if v_ma <= 0:
            return Signal(action="hold", size=0.0, reason="volume_ma_zero")
        v_last = float(volume.iloc[-1])
        ratio = v_last / v_ma
        if ratio < self.volume_ratio_min:
            return Signal(
                action="hold", size=0.0,
                reason=f"volume_gate:ratio={ratio:.2f}<{self.volume_ratio_min:.2f}",
            )

        # Airborne core (v1): breakout + 40% retracement.
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
                    f"airborne_v3_pending:bo@-{bars_since},"
                    f"base={setup.base:.4f},ext={setup.extreme:.4f},"
                    f"trig={trigger:.4f},c={c_now:.4f},trend_ok,vol_ok"
                ),
            )

        return Signal(
            action="buy",
            size=self.default_size,
            reason=(
                f"airborne_v3_fire:bo@-{bars_since},"
                f"base={setup.base:.4f},ext={setup.extreme:.4f},"
                f"trig={trigger:.4f},c={c_now:.4f}>sma{self.trend_sma_period}={trend:.4f},"
                f"vol_ratio={ratio:.2f},retrace={RETRACE_RATIO}"
            ),
        )
