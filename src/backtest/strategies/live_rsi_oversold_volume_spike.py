"""Live universe-scanner: RSI oversold + volume spike (#227 S1).

Per-symbol entry rule:
    RSI(14) < 30  AND  volume[-1] > 2 * mean(volume[-21:-1])

Exit is delegated to ``LivePositionRiskManager`` (#227 S2). This strategy
only emits ``buy`` signals; ``sell`` is never returned.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin


class LiveRsiOversoldVolumeSpike(LiveScannerMixin):
    """Stateless per-symbol oversold + volume spike detector.

    The strategy is stateless across ticks — bar-boundary gating, session
    hours, and universe membership are enforced by the live loop / snapshot
    builder before ``on_bar`` is invoked.

    Sizing:
        ``default_size`` (default 0.05) is returned as ``Signal.size`` —
        a fraction-of-equity. The orchestrator + ``risk.evaluate`` apply the
        portfolio-level concentration limits.
    """

    required_factors: ClassVar[list[str]] = ["rsi"]

    RSI_PERIOD: ClassVar[int] = 14
    RSI_THRESHOLD: ClassVar[float] = 30.0
    VOLUME_LOOKBACK: ClassVar[int] = 20
    VOLUME_MULTIPLIER: ClassVar[float] = 2.0
    MIN_HISTORY: ClassVar[int] = 21  # VOLUME_LOOKBACK + 1

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
        # 레버리지 트레이딩용 ROI 기반 익절/손절 (정적 pct 보다 우선).
        self._apply_roi_targets(
            take_profit_roi=take_profit_roi,
            stop_loss_roi=stop_loss_roi,
            leverage=leverage,
        )

    async def on_bar(self, ctx: object) -> Signal | None:
        # ctx is dict-shaped — match the convention used by momo_kis_v1 / breakout_donchian.
        snap = ctx["market_snapshot"]  # type: ignore[index]
        history: pd.DataFrame | None = snap.get("history")
        if history is None or len(history) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="warmup")

        factors = ctx.get("factors", {}) if isinstance(ctx, dict) else {}  # type: ignore[union-attr]
        rsi: pd.Series | None = factors.get("rsi") if isinstance(factors, dict) else None
        if rsi is None or len(rsi) == 0:
            return Signal(action="hold", size=0.0, reason="rsi_missing")

        last_rsi_raw = rsi.iloc[-1]
        if pd.isna(last_rsi_raw):
            return Signal(action="hold", size=0.0, reason="rsi_nan")
        last_rsi = float(last_rsi_raw)
        if last_rsi >= self.RSI_THRESHOLD:
            return Signal(
                action="hold", size=0.0,
                reason=f"rsi_above_threshold:{last_rsi:.1f}",
            )

        volume = history["volume"]
        last_volume = float(volume.iloc[-1])
        baseline_window = volume.iloc[-(self.VOLUME_LOOKBACK + 1):-1]
        if len(baseline_window) < self.VOLUME_LOOKBACK:
            return Signal(action="hold", size=0.0, reason="volume_baseline_short")
        volume_ma = float(baseline_window.mean())
        if volume_ma <= 0:
            return Signal(action="hold", size=0.0, reason="volume_ma_zero")

        ratio = last_volume / volume_ma
        if ratio < self.VOLUME_MULTIPLIER:
            return Signal(
                action="hold", size=0.0,
                reason=f"volume_ratio_low:{ratio:.2f}",
            )

        # Confidence ∈ [0, 1] — combines RSI distance below threshold and volume excess.
        rsi_factor = (self.RSI_THRESHOLD - last_rsi) / self.RSI_THRESHOLD
        vol_factor = ratio / self.VOLUME_MULTIPLIER  # >= 1
        confidence = max(0.0, min(1.0, rsi_factor * (vol_factor - 1.0 + 0.5)))

        return Signal(
            action="buy",
            size=self.default_size,
            reason=f"rsi_oversold_volume_spike:rsi={last_rsi:.1f},vol_ratio={ratio:.2f}",
            confidence=confidence,
        )
