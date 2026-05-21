"""Live-scanner paradigm helpers (#227).

Live-scanner strategies are evaluated *per-symbol on every tick* — a single
``on_bar(ctx)`` call receives one symbol's snapshot and returns at most one
``Signal``. The orchestrator (``AsyncStrategyOrchestrator.run_bar``) detects
these strategies via the ``is_live_scanner`` class attribute and iterates
``market_snapshot["ohlcv_history"]`` (``dict[str, pd.DataFrame]``), dispatching
once per symbol.

Position-level exits (stop_loss / take_profit / trailing_stop) are NOT the
strategy's responsibility. The strategy only emits ``buy`` signals; exits are
enforced by ``LivePositionRiskManager`` (S2) consuming the class attributes
declared here.

This is the third paradigm alongside ``universe-scan`` (cross-sectional
ranking, weekly rebal — see ``docs/specs/universe-scan-strategy-pattern.md``)
and ``single-ticker`` (legacy, e.g. ``momo_btc_v2``).
"""
from __future__ import annotations

from typing import ClassVar


class LiveScannerMixin:
    """Marker mixin for live-scanner paradigm strategies.

    Subclasses inherit ``is_live_scanner = True`` which opts them into
    per-symbol dispatch in ``AsyncStrategyOrchestrator.run_bar``. Stop/TP
    thresholds declared here are consumed by ``LivePositionRiskManager``
    (added in S2 of #227); strategies themselves never emit ``sell`` signals.

    Subclass example:

        class LiveRsiOversoldVolumeSpike(LiveScannerMixin):
            stop_loss_pct: ClassVar[float] = 0.03
            take_profit_pct: ClassVar[float] = 0.06

            async def on_bar(self, ctx) -> Signal | None:
                snap = ctx["market_snapshot"]      # single-symbol snapshot
                history = snap["history"]
                ...
                return Signal(action="buy", size=0.05, reason="...")
    """

    is_live_scanner: ClassVar[bool] = True
    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06
    trailing_stop_pct: ClassVar[float | None] = None
    # 2026-05-21: stop/TP 청산 직후 같은 (sid, symbol) 재진입 차단 시간 (초).
    # Default 0.0 = 차단 없음 (기존 동작 보존). 0 보다 크면 orchestrator 가
    # `release_live_position()` 호출 시점에 monotonic 타임스탬프를 기록하고,
    # 그 시점 + cooldown 안에 들어오는 BUY 신호는 통과시키지 않는다.
    # Churn 방지용 (예: ATR breakout 직후 stop 맞고 즉시 재진입 반복).
    # production.yaml 의 strategy kwargs 로 override 가능.
    cooldown_after_stop_sec: ClassVar[float] = 0.0
