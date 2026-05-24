"""Live universe-scanner: Airborne BB-reversal (40% retracement from extreme).

Reverse-engineered from the external-lecture indicator "에어본(체험판)" (Pine
``PUB;0b920144158f4848ba5d506932a636d7``, v5). Mechanism and validation:
``docs/background/38-airborne-indicator-reverse-engineering.md``. Spec:
``docs/specs/strategies/live-airborne-bb-reversal.md``.

Per-symbol entry rule (long-only — project MVP policy):

    1. Find the most recent **long breakout** within ``MAX_LOOKBACK`` bars:
           ``low[i] <= bb_lower[i]``  AND  ``low[i-1] > bb_lower[i-1]``
    2. Establish ``base = close[i]`` and track ``extreme = min(low[i:])``.
    3. Setup is "active" if no intermediate confirmed bar has already crossed
       ``trigger = extreme + 0.4 * (base - extreme)`` upward.
    4. Fire long on the current bar's close iff ``close[-1] >= trigger`` (with
       current low folded into extreme).

Differentiator vs sibling ``live_mg_bb_reversal`` (rejected, all 16 R/R×freq
combos PF<1):
    The MG variant uses CANDLE PATTERN gates (engulfing/hammer) on the reclaim
    bar. The airborne mechanism uses a NUMERIC retracement ratio (40% from
    extreme). Pre-registered hypothesis: the numeric retracement filters false
    bounces better than the candle-structure filter — to be falsified or
    confirmed by 1y BTC+ETH 1h sweep. Game-theoretically distinct entry signal
    despite shared "BB breakout + mean reversion" lineage.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

import signals
from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from signals.airborne_bb_reversal import RETRACE_RATIO, evaluate_long_fire


class LiveAirborneBbReversal(LiveScannerMixin):
    BB_WINDOW: ClassVar[int] = 20
    BB_STD: ClassVar[float] = 2.0
    MAX_LOOKBACK: ClassVar[int] = 50  # max bars back to find the latest active setup
    # Derived to keep warmup in sync with BB window if either is bumped later.
    MIN_HISTORY: ClassVar[int] = BB_WINDOW + 2

    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06

    def __init__(
        self,
        *,
        default_size: float = 0.05,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
    ) -> None:
        if not 0 < default_size <= 1.0:
            raise ValueError(f"default_size must be in (0, 1], got {default_size}")
        self.default_size = default_size
        # Instance attrs shadow ClassVar — clean sweeps, no class-level mutation.
        if stop_loss_pct is not None:
            self.stop_loss_pct = stop_loss_pct
        if take_profit_pct is not None:
            self.take_profit_pct = take_profit_pct
        if trailing_stop_pct is not None:
            self.trailing_stop_pct = trailing_stop_pct

    async def on_bar(self, ctx: object) -> Signal | None:
        snap = ctx["market_snapshot"]  # type: ignore[index]
        history: pd.DataFrame | None = snap.get("history")
        if history is None or len(history) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="warmup")

        close = history["close"]
        bb = signals.compute(
            "bollinger", close=close, window=self.BB_WINDOW, n_std=self.BB_STD,
        )
        lower = bb["lower"]
        if pd.isna(lower.iloc[-1]) or pd.isna(lower.iloc[-2]):
            return Signal(action="hold", size=0.0, reason="bb_warmup")

        fires, setup, trigger = evaluate_long_fire(
            history=history,
            bb_lower=lower,
            max_lookback=self.MAX_LOOKBACK,
        )

        if setup is None:
            return Signal(action="hold", size=0.0, reason="no_active_setup")

        bars_since = len(history) - 1 - setup.breakout_index
        c_now = float(close.iloc[-1])

        if not fires:
            return Signal(
                action="hold", size=0.0,
                reason=(
                    f"airborne_long_pending:bo@-{bars_since},"
                    f"base={setup.base:.4f},ext={setup.extreme:.4f},"
                    f"trig={trigger:.4f},c={c_now:.4f}"
                ),
            )

        return Signal(
            action="buy",
            size=self.default_size,
            reason=(
                f"airborne_long_fire:bo@-{bars_since},"
                f"base={setup.base:.4f},ext={setup.extreme:.4f},"
                f"trig={trigger:.4f},c={c_now:.4f},ratio={RETRACE_RATIO}"
            ),
        )
