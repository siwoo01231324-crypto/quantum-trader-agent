"""Live universe-scanner: Airborne BB-reversal v1.1 — close-based breakout + filters.

v1 (high/low 기반 돌파) 가 사용자 라이브 차트 비교에서 원본보다 신호를 과다
발화함을 확인 → close-기반 + close margin + body margin 게이트로 정정. 결과
원본 에어본(체험판) 인디케이터와 신호 위치가 시각적으로 거의 일치.

차이점 vs v1:
    v1:   high >= bb_upper AND prev_high < prev_bb_upper                  (wick 도 잡음)
    v1.1: close > bb_upper * (1+margin) AND prev_close <= prev_thr
          AND body_pct >= min_body                                       (실제 돌파만)

기본 파라미터:
    min_close_margin = 0.001  (0.1% — close 와 BB 사이 최소 거리)
    min_body_pct     = 0.005  (0.5% — 돌파봉의 body 최소 크기)

이 값들은 라이브 차트 비교 + 1y BTC 1h 시뮬 (sweep_airborne_v11_params.py) 에서
신호 빈도가 사용자 차트의 원본 신호 빈도와 가장 가까운 조합으로 선정됨.

알파 한계 (자동 매매 미적합):
    sweep_band_riding_filters + bench_live_airborne_v11_5m_exit_v2 결과:
    7개 (cost × R/R) 조합에서 모든 현실적 비용 (2~4bp) 에서 PF<1. 비용 0
    (이상화) 에서만 PF=1.013~1.020 borderline. 인디케이터 자체 알파 ≈ 0.
    BB 평균회귀 가족의 구조적 음의 엣지 일관성 확인.

본 코드는 *재현 정확도* 가 목적 — 시각 가이드 / 알람 신호 / 역공학 보존본.
자동 매매 의존 금지. 사양: docs/specs/strategies/live-airborne-bb-reversal.md
v1.1 섹션 + docs/specs/strategies/live-airborne-bb-reversal-v11.md (이 spec).
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

import signals
from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from signals.airborne_bb_reversal import RETRACE_RATIO


class LiveAirborneBbReversalV11(LiveScannerMixin):
    BB_WINDOW: ClassVar[int] = 20
    BB_STD: ClassVar[float] = 2.0
    MAX_LOOKBACK: ClassVar[int] = 50
    MIN_CLOSE_MARGIN: ClassVar[float] = 0.001  # 0.1%
    MIN_BODY_PCT: ClassVar[float] = 0.005       # 0.5%
    MIN_HISTORY: ClassVar[int] = BB_WINDOW + 2

    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06

    def __init__(
        self,
        *,
        default_size: float = 0.05,
        min_close_margin: float | None = None,
        min_body_pct: float | None = None,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
    ) -> None:
        if not 0 < default_size <= 1.0:
            raise ValueError(f"default_size must be in (0, 1], got {default_size}")
        self.default_size = default_size
        self.min_close_margin = (
            min_close_margin if min_close_margin is not None else self.MIN_CLOSE_MARGIN
        )
        if self.min_close_margin < 0:
            raise ValueError(f"min_close_margin >= 0 required, got {self.min_close_margin}")
        self.min_body_pct = (
            min_body_pct if min_body_pct is not None else self.MIN_BODY_PCT
        )
        if self.min_body_pct < 0:
            raise ValueError(f"min_body_pct >= 0 required, got {self.min_body_pct}")
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
        open_ = history["open"]
        low = history["low"]
        bb = signals.compute(
            "bollinger", close=close, window=self.BB_WINDOW, n_std=self.BB_STD,
        )
        lower = bb["lower"]
        if pd.isna(lower.iloc[-1]) or pd.isna(lower.iloc[-2]):
            return Signal(action="hold", size=0.0, reason="bb_warmup")

        # v1.1 gates: close-based + margin + body
        n = len(history)
        lower_thr = lower * (1 - self.min_close_margin)
        body_pct = (close - open_).abs() / open_.where(open_ != 0, 1.0)

        # Find most recent active long setup (close-based break, not high/low)
        breakout_i = None
        for i in range(n - 2, max(n - self.MAX_LOOKBACK - 1, 0), -1):
            if i - 1 < 0 or pd.isna(lower_thr.iloc[i]) or pd.isna(lower_thr.iloc[i - 1]):
                continue
            if (float(close.iloc[i]) < float(lower_thr.iloc[i])
                and float(close.iloc[i - 1]) >= float(lower_thr.iloc[i - 1])
                and float(body_pct.iloc[i]) >= self.min_body_pct):
                breakout_i = i
                break

        if breakout_i is None:
            return Signal(action="hold", size=0.0, reason="no_breakout_v11")

        base = float(close.iloc[breakout_i])
        extreme = float(low.iloc[breakout_i])

        # Track extreme through bars after breakout (excluding current bar)
        for j in range(breakout_i + 1, n - 1):
            extreme = min(extreme, float(low.iloc[j]))
            trig_j = extreme + RETRACE_RATIO * (base - extreme)
            if float(close.iloc[j]) >= trig_j:
                return Signal(action="hold", size=0.0, reason=f"setup_terminated@-{n-1-j}")

        # Evaluate current bar
        extreme = min(extreme, float(low.iloc[-1]))
        trigger = extreme + RETRACE_RATIO * (base - extreme)
        c_now = float(close.iloc[-1])
        bars_since = n - 1 - breakout_i

        if c_now >= trigger:
            return Signal(
                action="buy", size=self.default_size,
                reason=(
                    f"airborne_v11_fire:bo@-{bars_since},base={base:.4f},"
                    f"ext={extreme:.4f},trig={trigger:.4f},c={c_now:.4f},"
                    f"margin={self.min_close_margin},body={self.min_body_pct}"
                ),
            )
        return Signal(
            action="hold", size=0.0,
            reason=(
                f"airborne_v11_pending:bo@-{bars_since},base={base:.4f},"
                f"ext={extreme:.4f},trig={trigger:.4f},c={c_now:.4f}"
            ),
        )
