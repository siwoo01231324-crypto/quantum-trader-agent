"""Live universe-scanner: 20-bar high breakout with ATR-based trailing exit (#227 S4).

Per-symbol entry rule:
    close[-1] >= max(close[-21:-1])   (20-bar high breakout)

Exit policy: this strategy declares a non-null ``trailing_stop_pct`` so the
``LivePositionRiskManager`` lets price ride and only exits on a 4% pullback
from the running peak. ``stop_loss_pct`` and ``take_profit_pct`` are set
generously so the trailing rule dominates intraday exits — see spec md
``docs/specs/strategies/live-breakout-with-atr-stop.md``.

2026-05-21 — ATR 기반 동적 stop 추가: kwargs 로 ``stop_atr_mult`` (또는
``take_profit_atr_mult``, ``trailing_stop_atr_mult``) 가 주어지면 매 BUY 신호
시점에 history 의 ATR(period) 을 계산해 `entry × (1 - atr × mult / entry)`
관계로 *override* 를 Signal 에 실어 보낸다. risk manager 는 이 override 를
해당 (sid, symbol) 의 dynamic policy 로 저장 — 포지션 수명 동안 그 값으로
stop/TP/trailing 평가. fixed % stop 이 코인 정상 변동성보다 작아 매 진입이
노이즈로 stop 맞는 churn 손실 사례 (NEARUSDT 2026-05-21, ~$80) 의 fix.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin


def _calculate_atr(history: pd.DataFrame, period: int) -> float | None:
    """ATR(period) — high/low/close 로 단순 SMA 평균. 데이터 부족 시 None.

    TR = max(high-low, |high - prev_close|, |low - prev_close|). 첫 bar 는
    prev_close 없어 TR = high-low. period+1 bar 이상 필요.
    """
    if len(history) < period + 1:
        return None
    high = history["high"].astype(float).values
    low = history["low"].astype(float).values
    close = history["close"].astype(float).values
    prev_close = close[:-1]
    h = high[1:]
    l = low[1:]
    tr = [max(h[i] - l[i], abs(h[i] - prev_close[i]), abs(l[i] - prev_close[i]))
          for i in range(len(h))]
    if len(tr) < period:
        return None
    atr = sum(tr[-period:]) / float(period)
    return float(atr)


class LiveBreakoutWithAtrStop(LiveScannerMixin):
    BREAKOUT_LOOKBACK: ClassVar[int] = 20
    MIN_HISTORY: ClassVar[int] = 30

    # Trailing stop is the primary exit — give stop_loss / take_profit
    # generous bands so they only catch extreme outliers.
    stop_loss_pct: ClassVar[float] = 0.05
    take_profit_pct: ClassVar[float] = 0.20
    trailing_stop_pct: ClassVar[float] = 0.04

    # ATR 동적 stop default — None 이면 ClassVar/kwargs 의 정적 % 사용.
    stop_atr_mult: ClassVar[float | None] = None
    take_profit_atr_mult: ClassVar[float | None] = None
    trailing_stop_atr_mult: ClassVar[float | None] = None
    atr_period: ClassVar[int] = 14

    def __init__(
        self, *,
        default_size: float = 0.05,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
        cooldown_after_stop_sec: float | None = None,
        stop_atr_mult: float | None = None,
        take_profit_atr_mult: float | None = None,
        trailing_stop_atr_mult: float | None = None,
        atr_period: int | None = None,
    ) -> None:
        if not 0 < default_size <= 1.0:
            raise ValueError(f"default_size must be in (0, 1], got {default_size}")
        self.default_size = default_size
        if stop_loss_pct is not None:
            self.stop_loss_pct = stop_loss_pct
        if take_profit_pct is not None:
            self.take_profit_pct = take_profit_pct
        if trailing_stop_pct is not None:
            self.trailing_stop_pct = trailing_stop_pct
        if cooldown_after_stop_sec is not None:
            if cooldown_after_stop_sec < 0:
                raise ValueError(
                    f"cooldown_after_stop_sec must be >= 0, got {cooldown_after_stop_sec}"
                )
            self.cooldown_after_stop_sec = cooldown_after_stop_sec
        for name, value in (
            ("stop_atr_mult", stop_atr_mult),
            ("take_profit_atr_mult", take_profit_atr_mult),
            ("trailing_stop_atr_mult", trailing_stop_atr_mult),
        ):
            if value is not None:
                if value <= 0:
                    raise ValueError(f"{name} must be > 0, got {value}")
                setattr(self, name, value)
        if atr_period is not None:
            if atr_period < 2:
                raise ValueError(f"atr_period must be >= 2, got {atr_period}")
            self.atr_period = atr_period

    async def on_bar(self, ctx: object) -> Signal | None:
        snap = ctx["market_snapshot"]  # type: ignore[index]
        history: pd.DataFrame | None = snap.get("history")
        if history is None or len(history) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="warmup")

        close = history["close"]
        baseline = close.iloc[-(self.BREAKOUT_LOOKBACK + 1):-1]
        if len(baseline) < self.BREAKOUT_LOOKBACK:
            return Signal(action="hold", size=0.0, reason="baseline_short")
        prior_max = float(baseline.max())
        last_close = float(close.iloc[-1])
        if last_close < prior_max:
            return Signal(
                action="hold", size=0.0,
                reason=f"no_breakout:last={last_close:.0f},max={prior_max:.0f}",
            )

        # ATR 기반 동적 stop/TP/trailing 거리 계산. mult 가 설정되어 있고 ATR
        # 계산 가능할 때만 override 를 채움. ATR 부족하거나 mult 미설정이면
        # None → risk manager 가 정적 % 로 fallback (기존 동작).
        atr_overrides = self._atr_overrides(history, last_close)

        return Signal(
            action="buy",
            size=self.default_size,
            reason=(
                f"atr_breakout:last={last_close:.0f},max20={prior_max:.0f},"
                f"trailing_pct={self.trailing_stop_pct:.2%}"
                + (f",atr_stop={atr_overrides['stop']:.4f}" if atr_overrides.get("stop") else "")
            ),
            stop_loss_pct_override=atr_overrides.get("stop"),
            take_profit_pct_override=atr_overrides.get("tp"),
            trailing_stop_pct_override=atr_overrides.get("trail"),
        )

    def _atr_overrides(
        self, history: pd.DataFrame, last_close: float,
    ) -> dict[str, float | None]:
        """진입 시점의 ATR 로 stop/TP/trailing distance 를 *현재가 대비 비율* 로
        환산해 dict 반환. 어느 키든 mult 미설정 또는 ATR 계산 실패면 None.
        """
        out: dict[str, float | None] = {"stop": None, "tp": None, "trail": None}
        if (self.stop_atr_mult is None
                and self.take_profit_atr_mult is None
                and self.trailing_stop_atr_mult is None):
            return out
        if last_close <= 0:
            return out
        atr = _calculate_atr(history, int(self.atr_period))
        if atr is None or atr <= 0:
            return out

        def _to_pct(mult: float | None) -> float | None:
            if mult is None or mult <= 0:
                return None
            # pct of entry price; risk manager 가 `entry * (1 - pct)` 로 stop 계산.
            return min(0.999, atr * float(mult) / last_close)
        out["stop"] = _to_pct(self.stop_atr_mult)
        out["tp"] = _to_pct(self.take_profit_atr_mult)
        out["trail"] = _to_pct(self.trailing_stop_atr_mult)
        return out
