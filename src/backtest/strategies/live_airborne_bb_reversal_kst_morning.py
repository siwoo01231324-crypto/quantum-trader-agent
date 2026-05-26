"""Live-scanner: Pine v1.2 airborne BB-reversal (bidir) + KST 06–12 시간 필터.

[[live-airborne-bb-reversal-v11]] 의 Pine v1.2 close-기반 + ATR-적응 body
게이트를 **bidirectional** (long + short) 으로 구현. 그 위에 KST 06:00–11:59
구간 진입 게이트를 얹는다. 사용자 요청 (2026-05-26):

- daemon (qta-airborne-daemon, Telegram 알림) 의 누적 4 일치 339 FIRE 시뮬에서
  06–12 KST 블록이 PF 3.07 / +47.3% — 다른 시간대 (PF 0.74/0.86/1.35) 대비
  비대칭 알파.
- 누적 통계상 **SHORT (PF 2.16) 가 전체 net 알파의 80%** — long-only 로는
  거의 다 놓침. 그래서 v0 (long-only) 가 아닌 v1.2 bidir 를 base 로 한다.

## 데몬과 분리 (사용자 명시 요청)

`scripts/airborne_alert_daemon.py` 의 Telegram FIRE 알림은 24h 모든 시각에
그대로 발화 — 본 전략과 완전 독립. 본 전략은 같은 signal 모듈을 orchestrator
안에서 직접 호출하므로 daemon 코드/설정 일체 무수정.

## 발화 규칙

매 봉 확정 close 시:

1. KST hour 게이트: `bar_close_kst.hour ∈ {6,7,8,9,10,11}` 아니면 hold.
2. v1.2 long signal: `evaluate_long_fire_v11(history, bb_lower=...)`
3. v1.2 short signal: `evaluate_short_fire_v11(history, bb_upper=...)`
4. 우선순위: long fire 와 short fire 동시 발화는 v1.2 state machine 상
   불가능 (한 setup 만 active). 안전 fallback 으로 long 우선.

청산은 LivePositionRiskManager 가 stop_loss_pct / take_profit_pct 로 처리 —
시간 무관 (13 시 KST 에 닿아도 즉시 청산).

## 파라미터

| 항목 | 값 | 출처 |
|---|---|---|
| min_close_margin | 0.001 (0.1%) | Pine v1.2 input default |
| atr_period | 14 | Pine v1.2 input default |
| atr_body_mult | 0.6 | Pine v1.2 input default |
| BB_WINDOW | 20 | Pine v1.2 input default |
| BB_STD | 2.0 | Pine v1.2 input default |
| stop_loss_pct | 0.03 | 5y bench gate sweep 가 default 로 검증 |
| take_profit_pct | 0.06 | 1:2 손익비 (live-scanner 공통) |
| kst_entry_hours | {6,7,8,9,10,11} | 데몬 누적 PF 3.07 블록 |
"""
from __future__ import annotations

from typing import ClassVar
from zoneinfo import ZoneInfo

import pandas as pd

import signals
from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from signals.airborne_bb_reversal import (
    DEFAULT_ATR_BODY_MULT_V11,
    DEFAULT_ATR_PERIOD_V11,
    DEFAULT_MIN_CLOSE_MARGIN_V11,
    RETRACE_RATIO,
    evaluate_long_fire_v11,
    evaluate_short_fire_v11,
)

_KST = ZoneInfo("Asia/Seoul")
_KST_MORNING_HOURS: frozenset[int] = frozenset({6, 7, 8, 9, 10, 11})


def _bar_hour_kst(history: pd.DataFrame) -> int | None:
    """history 마지막 봉의 마감 시각을 KST hour 로 반환. 실패 시 None."""
    if history is None or len(history) == 0:
        return None
    last_ts = history.index[-1]
    try:
        ts = pd.Timestamp(last_ts)
        if ts.tz is None:
            ts = ts.tz_localize("UTC")
        return ts.tz_convert(_KST).hour
    except (TypeError, ValueError, AttributeError):
        return None


class LiveAirborneBbReversalKstMorning(LiveScannerMixin):
    """Pine v1.2 (bidir, ATR-adaptive body) + KST 06–12 morning gate."""

    BB_WINDOW: ClassVar[int] = 20
    BB_STD: ClassVar[float] = 2.0
    MAX_LOOKBACK: ClassVar[int] = 50
    MIN_HISTORY: ClassVar[int] = max(BB_WINDOW + 2,
                                     DEFAULT_ATR_PERIOD_V11 + 1)

    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06

    kst_entry_hours: ClassVar[frozenset[int]] = _KST_MORNING_HOURS

    def __init__(
        self,
        *,
        default_size: float = 0.05,
        min_close_margin: float = DEFAULT_MIN_CLOSE_MARGIN_V11,
        atr_period: int = DEFAULT_ATR_PERIOD_V11,
        atr_body_mult: float = DEFAULT_ATR_BODY_MULT_V11,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
        kst_entry_hours: tuple[int, ...] | list[int] | None = None,
    ) -> None:
        if not 0 < default_size <= 1.0:
            raise ValueError(f"default_size must be in (0, 1], got {default_size}")
        if min_close_margin < 0:
            raise ValueError(f"min_close_margin >= 0 required, got {min_close_margin}")
        if atr_period < 1:
            raise ValueError(f"atr_period >= 1 required, got {atr_period}")
        if atr_body_mult < 0:
            raise ValueError(f"atr_body_mult >= 0 required, got {atr_body_mult}")

        self.default_size = default_size
        self.min_close_margin = float(min_close_margin)
        self.atr_period = int(atr_period)
        self.atr_body_mult = float(atr_body_mult)

        if stop_loss_pct is not None:
            self.stop_loss_pct = stop_loss_pct
        if take_profit_pct is not None:
            self.take_profit_pct = take_profit_pct
        if trailing_stop_pct is not None:
            self.trailing_stop_pct = trailing_stop_pct

        if kst_entry_hours is not None:
            invalid = [h for h in kst_entry_hours if not (0 <= int(h) <= 23)]
            if invalid:
                raise ValueError(
                    f"kst_entry_hours must be in [0,23], got invalid={invalid}",
                )
            self.kst_entry_hours = frozenset(int(h) for h in kst_entry_hours)

    async def on_bar(self, ctx: object) -> Signal | None:
        snap = ctx["market_snapshot"]  # type: ignore[index]
        history: pd.DataFrame | None = snap.get("history")
        if history is None or len(history) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="warmup")

        # ── KST 시간 게이트 (entry only — 청산은 시간 무관) ─────────────
        hour_kst = _bar_hour_kst(history)
        if hour_kst is not None and hour_kst not in self.kst_entry_hours:
            return Signal(
                action="hold", size=0.0,
                reason=f"time_filter:kst_hour={hour_kst}_not_in_morning",
            )

        # ── v1.2 bidir signal evaluation ────────────────────────────────
        close = history["close"]
        bb = signals.compute(
            "bollinger", close=close, window=self.BB_WINDOW, n_std=self.BB_STD,
        )
        lower = bb["lower"]
        upper = bb["upper"]
        if (pd.isna(lower.iloc[-1]) or pd.isna(lower.iloc[-2])
                or pd.isna(upper.iloc[-1]) or pd.isna(upper.iloc[-2])):
            return Signal(action="hold", size=0.0, reason="bb_warmup")

        long_fires, long_setup, long_trig = evaluate_long_fire_v11(
            history=history,
            bb_lower=lower,
            max_lookback=self.MAX_LOOKBACK,
            min_close_margin=self.min_close_margin,
            atr_period=self.atr_period,
            atr_body_mult=self.atr_body_mult,
        )
        short_fires, short_setup, short_trig = evaluate_short_fire_v11(
            history=history,
            bb_upper=upper,
            max_lookback=self.MAX_LOOKBACK,
            min_close_margin=self.min_close_margin,
            atr_period=self.atr_period,
            atr_body_mult=self.atr_body_mult,
        )

        c_now = float(close.iloc[-1])

        if long_fires:
            bars_since = (
                len(history) - 1 - long_setup.breakout_index
                if long_setup is not None else -1
            )
            return Signal(
                action="buy",
                size=self.default_size,
                reason=(
                    f"airborne_v12_long_fire:bo@-{bars_since},"
                    f"base={long_setup.base:.4f},ext={long_setup.extreme:.4f},"
                    f"trig={long_trig:.4f},c={c_now:.4f},"
                    f"ratio={RETRACE_RATIO},kst={hour_kst}"
                ),
            )
        if short_fires:
            bars_since = (
                len(history) - 1 - short_setup.breakout_index
                if short_setup is not None else -1
            )
            return Signal(
                action="sell",
                size=self.default_size,
                reason=(
                    f"airborne_v12_short_fire:bo@-{bars_since},"
                    f"base={short_setup.base:.4f},ext={short_setup.extreme:.4f},"
                    f"trig={short_trig:.4f},c={c_now:.4f},"
                    f"ratio={RETRACE_RATIO},kst={hour_kst}"
                ),
            )

        # 게이트 통과 + 시그널 없음 — 어떤 setup 이 활성인지 reason 에 표시
        if long_setup is not None:
            return Signal(
                action="hold", size=0.0,
                reason=f"airborne_v12_long_pending:trig={long_trig:.4f},c={c_now:.4f}",
            )
        if short_setup is not None:
            return Signal(
                action="hold", size=0.0,
                reason=f"airborne_v12_short_pending:trig={short_trig:.4f},c={c_now:.4f}",
            )
        return Signal(action="hold", size=0.0, reason="no_active_setup")
