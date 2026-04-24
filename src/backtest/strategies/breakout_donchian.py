"""KOSPI200 Donchian channel breakout strategy (KRX 1d bars)."""
from __future__ import annotations

from datetime import time
from typing import ClassVar

import numpy as np
import pandas as pd

from backtest.protocol import AsyncStrategy, Signal
from risk.sizing import ewma_sigma, fractional_kelly, kelly_continuous
import signals


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


class BreakoutDonchian:
    """KOSPI200 Donchian channel breakout — KRX daily bars.

    Tunable parameters (param grid <= 3 axes):
      - entry_window: Donchian entry lookback (default 20)
      - exit_window: Donchian exit lookback (default 10)
      - kelly_k: fractional Kelly multiplier (default 0.5)

    Entry: close > upper_t = rolling_max(high, entry_window).shift(1).
           Multiple breakouts → rank by (close - upper_t) / atr_14.shift(1) desc → top-N slots.
    Exit:  close < lower_t = rolling_min(low, exit_window).shift(1).
    Bar boundary: KRX 장마감 15:30 KST, 평일, 비휴장일.
    Symbol: "KOSPI200_BASKET" (포트폴리오 레벨 single Signal).
    Instrument type: krx.
    """

    required_factors: ClassVar[list[str]] = ["donchian", "atr"]

    SYMBOL = "KOSPI200_BASKET"
    MIN_HISTORY = 21  # entry_window + 1

    def __init__(
        self,
        *,
        entry_window: int = 20,
        exit_window: int = 10,
        kelly_k: float = 0.5,
        top_n: int = 10,
        vol_target_annual: float = 0.15,
        universe_codes: list[str] | None = None,
    ) -> None:
        self.entry_window = entry_window
        self.exit_window = exit_window
        self.kelly_k = kelly_k
        self.top_n = top_n
        self.vol_target_annual = vol_target_annual
        self._active_slots: list[str] = []  # currently held stock codes
        self._universe_codes: list[str] | None = universe_codes

    def _is_my_bar_boundary(self, ts: pd.Timestamp) -> bool:
        try:
            from universe.krx_calendar import is_krx_holiday, KST
        except ImportError:
            import pytz
            KST = pytz.timezone("Asia/Seoul")
            is_krx_holiday = lambda d: False
        import pytz
        if ts.tzinfo is not None:
            ts_kst = ts.astimezone(KST)
        else:
            ts_kst = ts
        return (
            ts_kst.time() == time(15, 30)
            and ts_kst.weekday() < 5
            and not is_krx_holiday(ts_kst.date())
        )

    def _rank_breakout_candidates(
        self,
        ohlcv_history: dict[str, pd.DataFrame],
    ) -> list[tuple[str, float]]:
        """Return (code, breakout_strength) sorted desc — only genuine breakouts."""
        candidates: list[tuple[str, float]] = []
        for code, hist in ohlcv_history.items():
            if len(hist) < self.entry_window + 1:
                continue
            high = hist["high"]
            low = hist["low"]
            close = hist["close"]
            donchian = signals.compute("donchian", high=high, low=low, window=self.entry_window)
            atr_series = signals.compute("atr", high=high, low=low, close=close, window=14)

            upper = donchian["upper"].shift(1)
            atr_prev = atr_series.shift(1)

            last_close = float(close.iloc[-1])
            last_upper = float(upper.iloc[-1]) if not pd.isna(upper.iloc[-1]) else float("inf")
            last_atr = float(atr_prev.iloc[-1]) if not pd.isna(atr_prev.iloc[-1]) else 0.0

            if last_close > last_upper and last_atr > 0:
                strength = (last_close - last_upper) / last_atr
                candidates.append((code, strength))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates

    def _check_exits(
        self,
        ohlcv_history: dict[str, pd.DataFrame],
    ) -> list[str]:
        """Return codes in active slots that have crossed below exit lower band."""
        to_exit: list[str] = []
        for code in list(self._active_slots):
            hist = ohlcv_history.get(code)
            if hist is None or len(hist) < self.exit_window + 1:
                continue
            low = hist["low"]
            close = hist["close"]
            donchian_exit = signals.compute(
                "donchian", high=hist["high"], low=low, window=self.exit_window
            )
            lower_exit = donchian_exit["lower"].shift(1)
            last_close = float(close.iloc[-1])
            last_lower = float(lower_exit.iloc[-1]) if not pd.isna(lower_exit.iloc[-1]) else float("-inf")
            if last_close < last_lower:
                to_exit.append(code)
        return to_exit

    def _compute_basket_return(
        self,
        active_codes: list[str],
        ohlcv_history: dict[str, pd.DataFrame],
    ) -> float:
        """Equal-weight average of last-bar returns for active slots."""
        if not active_codes:
            return 0.0
        rets = []
        for code in active_codes:
            hist = ohlcv_history.get(code)
            if hist is not None and len(hist) >= 2:
                r = (hist["close"].iloc[-1] / hist["close"].iloc[-2]) - 1.0
                rets.append(r)
        return float(np.mean(rets)) if rets else 0.0

    def _entry_size(self, active_codes: list[str], ohlcv_history: dict[str, pd.DataFrame]) -> float:
        """Half-Kelly sizing based on basket returns vol."""
        if not active_codes:
            return 0.0
        basket_returns = []
        for code in active_codes:
            hist = ohlcv_history.get(code)
            if hist is not None and len(hist) >= 30:
                r = hist["close"].pct_change().dropna()
                basket_returns.append(r)
        if not basket_returns:
            return 1.0 / self.top_n * self.kelly_k

        combined = pd.concat(basket_returns, axis=1).mean(axis=1).dropna()
        if len(combined) < 2:
            return 1.0 / self.top_n * self.kelly_k

        sigma = ewma_sigma(combined, lam=0.94)
        mu = float(combined.mean())
        full = kelly_continuous(mu=mu, sigma=sigma)
        return fractional_kelly(full, k=self.kelly_k)

    async def on_bar(self, ctx: object) -> Signal | None:
        ts = ctx["ts"]
        if not self._is_my_bar_boundary(ts):
            return Signal(action="hold", size=0.0, reason="not my bar")

        snap = ctx["market_snapshot"]
        ohlcv_history: dict[str, pd.DataFrame] = snap.get("ohlcv_history", {})

        # Filter to universe if provided
        if self._universe_codes is not None:
            ohlcv_history = {k: v for k, v in ohlcv_history.items() if k in self._universe_codes}

        if not ohlcv_history:
            return Signal(action="hold", size=0.0, reason="insufficient history")

        # 1. Process exits
        exits = self._check_exits(ohlcv_history)
        for code in exits:
            if code in self._active_slots:
                self._active_slots.remove(code)

        # 2. Rank new breakout candidates — fill empty slots
        open_slots = self.top_n - len(self._active_slots)
        if open_slots > 0:
            candidates = self._rank_breakout_candidates(ohlcv_history)
            for code, _ in candidates:
                if open_slots <= 0:
                    break
                if code not in self._active_slots:
                    self._active_slots.append(code)
                    open_slots -= 1

        if not self._active_slots:
            return Signal(action="hold", size=0.0, reason="no active positions")

        # 3. Compute basket-level signal
        size = self._entry_size(self._active_slots, ohlcv_history)

        # Confidence: mean breakout strength of active slots (clipped)
        confidence_vals = []
        for code in self._active_slots:
            hist = ohlcv_history.get(code)
            if hist is not None and len(hist) >= self.entry_window + 1:
                high = hist["high"]
                low = hist["low"]
                close = hist["close"]
                donchian = signals.compute("donchian", high=high, low=low, window=self.entry_window)
                atr_series = signals.compute("atr", high=high, low=low, close=close, window=14)
                upper = donchian["upper"].shift(1)
                atr_prev = atr_series.shift(1)
                last_close = float(close.iloc[-1])
                last_upper = float(upper.iloc[-1]) if not pd.isna(upper.iloc[-1]) else last_close
                last_atr = float(atr_prev.iloc[-1]) if not pd.isna(atr_prev.iloc[-1]) else 1.0
                if last_atr > 0:
                    confidence_vals.append(_clip01(abs(last_close - last_upper) / last_atr))

        confidence = float(np.mean(confidence_vals)) if confidence_vals else 0.0

        # Expected return: 60-day basket return mean
        basket_rets = []
        for code in self._active_slots:
            hist = ohlcv_history.get(code)
            if hist is not None and len(hist) >= 60:
                r = hist["close"].pct_change().dropna().iloc[-60:]
                basket_rets.append(r)
        if basket_rets:
            combined = pd.concat(basket_rets, axis=1).mean(axis=1).dropna()
            expected_return = float(combined.mean()) if len(combined) > 0 else 0.0
        else:
            expected_return = 0.0

        action = "buy" if exits or open_slots < self.top_n else "hold"
        if len(self._active_slots) > 0:
            action = "buy"

        return Signal(
            action=action,
            size=size,
            reason=f"donchian breakout basket n={len(self._active_slots)}",
            confidence=confidence,
            expected_return=expected_return,
        )
