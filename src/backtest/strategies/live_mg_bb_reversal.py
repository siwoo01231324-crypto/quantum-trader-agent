"""Live universe-scanner: BB lower-band reversal with engulfing or hammer pattern.

Per-symbol entry rule (all gates must hold):
    1. Sustained dip near band: low touched/breached bb_lower on any of bars
       [-3, -2]  → represents the lecture's "4h breakout → wait" trigger
       collapsed into a single backtest timeframe.
    2. Reclaim back inside band: bb_lower[-1] < close[-1] < bb_upper[-1].
    3. Reversal candle on bar [-1]: bullish engulfing OR hammer.

Differentiator vs ``live_bb_lower_bounce`` (rejected, PF=0.922 / exp<0):
replaces the volume-MA confirm with an explicit price-action reversal
(engulfing/hammer). Pre-registered hypothesis — requiring candle structure
at the band filters out the "단순 reclaim → false bounce" failures that
drove PF<1 in the naive variant.

Source: external lecture (MG / Momentum Gap, 2026-05-19) distilled in
repo-root ``external-trading-lecture-techniques.md``. Experimental — do
not enable in production.yaml until 5y PF > 1 AND expectancy > 0 on
``scripts/eval_live_scanners_5y.py``.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

import signals
from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin


class LiveMgBbReversal(LiveScannerMixin):
    BB_WINDOW: ClassVar[int] = 20
    BB_STD: ClassVar[float] = 2.0
    DIP_LOOKBACK: ClassVar[int] = 2  # check bars [-2..-(DIP_LOOKBACK+1)] for band touch
    # Derived so changing DIP_LOOKBACK/BB_WINDOW doesn't silently desync warmup.
    MIN_HISTORY: ClassVar[int] = BB_WINDOW + DIP_LOOKBACK + 2

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
        # Instance attrs shadow ClassVar — keeps backtest sweeps clean (no
        # external attr mutation, no class-level state leak across runs).
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
        upper = bb["upper"]
        if pd.isna(lower.iloc[-1]) or pd.isna(lower.iloc[-(self.DIP_LOOKBACK + 1)]):
            return Signal(action="hold", size=0.0, reason="bb_warmup")

        # Gate 1: sustained dip — any prior bar in window touched the lower band.
        lows = history["low"]
        touched_at: int | None = None
        for k in range(2, self.DIP_LOOKBACK + 2):  # bars -2 .. -(DIP_LOOKBACK+1)
            if float(lows.iloc[-k]) <= float(lower.iloc[-k]):
                touched_at = k
                break
        if touched_at is None:
            return Signal(action="hold", size=0.0, reason="no_band_touch")

        # Gate 2: current bar reclaimed inside the band (not over-extended past upper).
        c_now = float(close.iloc[-1])
        l_now = float(lower.iloc[-1])
        u_now = float(upper.iloc[-1])
        if not (l_now < c_now < u_now):
            return Signal(
                action="hold", size=0.0,
                reason=(
                    f"no_reclaim:c_now={c_now:.4f}/"
                    f"l_now={l_now:.4f}/u_now={u_now:.4f}"
                ),
            )

        # Gate 3: reversal candle on bar [-1] — bullish engulfing OR hammer.
        o_now = float(history["open"].iloc[-1])
        h_now = float(history["high"].iloc[-1])
        lo_now = float(history["low"].iloc[-1])
        o_prev = float(history["open"].iloc[-2])
        c_prev = float(close.iloc[-2])

        engulfing = self._is_bullish_engulfing(
            o_prev=o_prev, c_prev=c_prev, o_now=o_now, c_now=c_now,
        )
        hammer = self._is_hammer(o=o_now, h=h_now, l=lo_now, c=c_now)
        if not (engulfing or hammer):
            return Signal(action="hold", size=0.0, reason="no_reversal_candle")

        pattern = "engulfing" if engulfing else "hammer"
        return Signal(
            action="buy",
            size=self.default_size,
            reason=(
                f"mg_bb_reversal:{pattern},"
                f"dip@-{touched_at},"
                f"c_now={c_now:.4f}>l_now={l_now:.4f}"
            ),
        )

    @staticmethod
    def _is_bullish_engulfing(
        *, o_prev: float, c_prev: float, o_now: float, c_now: float,
    ) -> bool:
        """Prior bar bearish + current bar bullish that engulfs the prior body."""
        prev_bearish = c_prev < o_prev
        now_bullish = c_now > o_now
        if not (prev_bearish and now_bullish):
            return False
        return o_now <= c_prev and c_now >= o_prev

    @staticmethod
    def _is_hammer(*, o: float, h: float, l: float, c: float) -> bool:
        """Long lower shadow (>=2x body), small upper shadow (<=body), body > 0."""
        body = abs(c - o)
        if body <= 0:
            return False
        lower_shadow = min(o, c) - l
        upper_shadow = h - max(o, c)
        if lower_shadow < 0 or upper_shadow < 0:
            return False
        return lower_shadow >= 2.0 * body and upper_shadow <= body
