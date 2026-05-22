"""Live universe-scanner: Bollinger lower-band bounce with volume confirmation (#227 S4).

Per-symbol entry rule:
    close[-2] < bb_lower[-2]              (prior bar pierced lower band)
    AND close[-1] > bb_lower[-1]          (current bar reclaimed the band)
    AND volume[-1] >= mean(volume[-21:-1])  (real buying, not noise)
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

import signals
from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin


class LiveBbLowerBounce(LiveScannerMixin):
    BB_WINDOW: ClassVar[int] = 20
    BB_STD: ClassVar[float] = 2.0
    VOLUME_LOOKBACK: ClassVar[int] = 20
    MIN_HISTORY: ClassVar[int] = 22  # BB_WINDOW + 2

    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06

    def __init__(
        self, *,
        default_size: float = 0.05,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
        take_profit_roi: float | None = None,
        stop_loss_roi: float | None = None,
        leverage: float | None = None,
        cooldown_after_stop_sec: float | None = None,
    ) -> None:
        if not 0 < default_size <= 1.0:
            raise ValueError(f"default_size must be in (0, 1], got {default_size}")
        self.default_size = default_size
        # Instance attrs shadow ClassVar — yaml-configurable stop/tp/trailing.
        if stop_loss_pct is not None:
            self.stop_loss_pct = stop_loss_pct
        if take_profit_pct is not None:
            self.take_profit_pct = take_profit_pct
        if trailing_stop_pct is not None:
            self.trailing_stop_pct = trailing_stop_pct
        # 레버리지 트레이딩용 ROI 기반 익절/손절 (정적 pct 보다 우선).
        self._apply_roi_targets(
            take_profit_roi=take_profit_roi,
            stop_loss_roi=stop_loss_roi,
            leverage=leverage,
        )
        # stop/TP 청산 직후 재진입 churn 차단 (수수료 폭증 방지).
        if cooldown_after_stop_sec is not None:
            if cooldown_after_stop_sec < 0:
                raise ValueError(
                    f"cooldown_after_stop_sec must be >= 0, got {cooldown_after_stop_sec}"
                )
            self.cooldown_after_stop_sec = cooldown_after_stop_sec

    async def on_bar(self, ctx: object) -> Signal | None:
        snap = ctx["market_snapshot"]  # type: ignore[index]
        history: pd.DataFrame | None = snap.get("history")
        if history is None or len(history) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="warmup")

        close = history["close"]
        bb_df = signals.compute(
            "bollinger", close=close, window=self.BB_WINDOW, n_std=self.BB_STD,
        )
        lower = bb_df["lower"]
        if pd.isna(lower.iloc[-1]) or pd.isna(lower.iloc[-2]):
            return Signal(action="hold", size=0.0, reason="bb_warmup")

        c_prev = float(close.iloc[-2])
        c_now = float(close.iloc[-1])
        l_prev = float(lower.iloc[-2])
        l_now = float(lower.iloc[-1])
        if not (c_prev < l_prev and c_now > l_now):
            return Signal(
                action="hold", size=0.0,
                reason=(
                    f"no_bb_bounce:c_prev={c_prev:.0f}/l_prev={l_prev:.0f},"
                    f"c_now={c_now:.0f}/l_now={l_now:.0f}"
                ),
            )

        volume = history["volume"]
        baseline = volume.iloc[-(self.VOLUME_LOOKBACK + 1):-1]
        if len(baseline) < self.VOLUME_LOOKBACK:
            return Signal(action="hold", size=0.0, reason="volume_baseline_short")
        v_ma = float(baseline.mean())
        if v_ma <= 0:
            return Signal(action="hold", size=0.0, reason="volume_ma_zero")
        v_last = float(volume.iloc[-1])
        ratio = v_last / v_ma
        if ratio < 1.0:
            return Signal(
                action="hold", size=0.0,
                reason=f"volume_weak:ratio={ratio:.2f}",
            )

        return Signal(
            action="buy",
            size=self.default_size,
            reason=(
                f"bb_lower_bounce:c_prev={c_prev:.0f}<l_prev={l_prev:.0f},"
                f"c_now={c_now:.0f}>l_now={l_now:.0f},vol_ratio={ratio:.2f}"
            ),
        )
