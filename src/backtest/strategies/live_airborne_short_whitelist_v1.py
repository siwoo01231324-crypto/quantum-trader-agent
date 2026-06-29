"""Live-scanner: SHORT-only airborne (Bitget/Binance 거래량 top-100 유니버스).

기존 ``LiveAirborneBbReversalKstHours`` (bidir) 를 상속하고 **SHORT 방향만**
발주하도록 ``on_bar`` 에서 short fire 만 평가한다. universe·게이트·진입 파라미터는
부모/production.yaml 을 따른다.

2026-06-07 #380 — 사용자 운영 결정으로 "고정 whitelist → 거래량 top-100 동적
universe" 로 전환. 텔레그램 airborne 알림이 잡는 종목(= 거래량 상위)을 그대로
숏 진입 ("다 사자"). get_universe override 제거 → 부모의 venue-routing top-100
(``QTA_BROKER_VENUE=bitget`` 이면 Bitget, 아니면 Binance) 상속. 진입 파라미터도
base airborne(retrace 0.4/atr 0.6) + TP1%/SL0.5% + 24h 게이트로 production.yaml
에서 override (검증 충돌 — spec 참조).

청산·warmup·BB·ATR·universe 로직은 부모 100% 재사용. 데몬 없이 orchestrator
안에서 direct dispatch.

원본 Hard OOS (19-symbol whitelist + 19h gate, **현재는 미사용**):
  test PF = 1.214, sumR = +1,395%, 5.45 trades/day

Spec: ``docs/specs/strategies/live-airborne-short-whitelist-v1.md``
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

import signals
from backtest.protocol import Signal
from backtest.strategies.live_airborne_bb_reversal_kst_hours import (
    LiveAirborneBbReversalKstHours,
)
from signals.airborne_bb_reversal import (
    evaluate_long_fire_v11,
    evaluate_short_fire_v11,
)

# 원본 Hard OOS 19시간 게이트 — 현재 production.yaml 이 24h 로 override 하므로
# 참고용으로만 보존. 제외: {4, 6, 7, 8, 13} (train PF<1).
_KST_HOURS_19: frozenset[int] = frozenset(
    {0, 1, 2, 3, 5, 9, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
)

# 원본 Hard OOS 진입 파라미터 — production.yaml 이 base airborne(0.4/0.6)으로
# override. ctor default 로만 잔존 (production.yaml 미지정 시 fallback).
_RETRACE_RATIO_HARD_OOS: float = 0.6
_ATR_BODY_MULT_HARD_OOS: float = 0.3


class LiveAirborneShortWhitelistV1(LiveAirborneBbReversalKstHours):
    """SHORT-only airborne — 거래량 top-100 유니버스 (#380 부터).

    get_universe 는 부모(``LiveAirborneBbReversalKstHours``)의 venue-routing
    top-100 을 상속. on_bar 은 short fire 만 평가해 SHORT-only 보장.

    원본 Hard OOS (19종 whitelist + 19h, 현재 미사용): test PF=1.214.
    Spec: docs/specs/strategies/live-airborne-short-whitelist-v1.md
    """

    strategy_id: ClassVar[str] = "live-airborne-short-whitelist-v1"

    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06
    shorts_allowed: ClassVar[bool] = True  # 부모 True 명시 — sell intent reduce_only=False
    # post-only Maker 진입 (2026-06-29). orchestrator._build_entry_intent 가 숏
    # *진입*(sell & not reduce_only)에 한해 이 속성을 읽어 GTX LIMIT(maker) stamp.
    # 청산은 항상 market(손절 즉시성). "market"=레거시. production.yaml kwargs override.
    entry_order_type: ClassVar[str] = "market"

    # 19시간 게이트 — Hard OOS train_PF>1 결과
    kst_entry_hours: ClassVar[frozenset[int]] = _KST_HOURS_19

    def __init__(
        self,
        *,
        default_size: float = 0.05,
        min_close_margin: float | None = None,
        atr_period: int | None = None,
        atr_body_mult: float = _ATR_BODY_MULT_HARD_OOS,
        retrace_ratio: float = _RETRACE_RATIO_HARD_OOS,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
        kst_entry_hours: tuple[int, ...] | list[int] | None = None,
        cooldown_after_stop_sec: float | None = None,
        max_concurrent_positions: int | None = None,
        entry_order_type: str | None = None,
    ) -> None:
        # 부모 ctor 호출 (min_close_margin / atr_period 는 None 이면 부모 default 사용)
        parent_kwargs: dict = {
            "default_size": default_size,
            "atr_body_mult": atr_body_mult,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "trailing_stop_pct": trailing_stop_pct,
            "kst_entry_hours": kst_entry_hours,
            "cooldown_after_stop_sec": cooldown_after_stop_sec,
            "max_concurrent_positions": max_concurrent_positions,
        }
        if min_close_margin is not None:
            parent_kwargs["min_close_margin"] = min_close_margin
        if atr_period is not None:
            parent_kwargs["atr_period"] = atr_period
        super().__init__(**parent_kwargs)

        if not (0 < retrace_ratio <= 1):
            raise ValueError(
                f"retrace_ratio in (0, 1] required, got {retrace_ratio}"
            )
        self.retrace_ratio = float(retrace_ratio)

        # post-only Maker 토글 — kwarg 지정 시 ClassVar override (instance 속성).
        # orchestrator 가 getattr 로 읽어 숏 진입에 GTX LIMIT stamp. 미지정/None 이면
        # ClassVar("market") 유지. 유효값만 허용("market"|"post_only").
        if entry_order_type is not None:
            if entry_order_type not in ("market", "post_only"):
                raise ValueError(
                    f"entry_order_type must be 'market'|'post_only', "
                    f"got {entry_order_type!r}"
                )
            self.entry_order_type = entry_order_type

    # get_universe 는 부모 상속 — venue-routing 거래량 top-100
    # (QTA_BROKER_VENUE=bitget → Bitget, 아니면 Binance). #380 부터 고정
    # whitelist yaml 미사용 (텔레그램 알림이 잡는 top-100 종목 = 숏 진입 대상).

    @classmethod
    def get_interval(cls) -> str:
        return "1h"

    async def on_bar(self, ctx: object) -> Signal | None:
        """부모와 동일한 구조이지만 *short fire 만* 발주.

        매 tick 평가 대신 *봉 마감 1회만* evaluate → 102종 × BB/ATR pandas
        계산 폭주 차단. 같은 봉 안에서는 캐시된 결과 즉시 반환.
        """
        # 봉마감 게이트 (live) — 형성봉 trim → 마감봉 평가 (KstHours 공유, #389).
        gated, closed_ts = self._bar_close_gate(ctx)
        if gated is None:
            return Signal(action="hold", size=0.0, reason="await_bar_close")
        ctx = gated
        snap = ctx["market_snapshot"]  # type: ignore[index]
        history: pd.DataFrame | None = snap.get("history")
        symbol = snap.get("symbol", "?")

        # consume 모드 — 자체평가 대신 데몬 발화(short)를 그대로 따라 진입
        # (거래 = 알림 100%, 입력데이터 차이로 인한 종목 불일치 제거). KstHours 공유.
        # 자체 BB 계산 안 하므로 warmup(MIN_HISTORY) 무관 — 마지막 봉 ts 만 필요.
        if (
            self._consume_enabled() and closed_ts is not None
            and history is not None and len(history) >= 1
        ):
            return self._consume_daemon_fire_on_bar(
                ctx, closed_ts, history, symbol, {"short"},
            )

        if history is None or len(history) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="warmup")

        # 봉 마감 캐시 (부모와 동일 정책) — instance 단위 dict
        if not hasattr(self, "_last_eval_bar_ts"):
            self._last_eval_bar_ts: dict[str, "pd.Timestamp"] = {}
            self._last_eval_signal: dict[str, "Signal | None"] = {}
        last_bar_ts = history.index[-1]
        cached_ts = self._last_eval_bar_ts.get(symbol)
        if cached_ts is not None and cached_ts == last_bar_ts:
            return self._last_eval_signal.get(symbol)

        # KST 시간 게이트 (부모와 동일 path)
        from backtest.strategies.live_airborne_bb_reversal_kst_morning import (
            _bar_hour_kst,
        )
        hour_kst = _bar_hour_kst(history)
        if hour_kst is not None and hour_kst not in self.kst_entry_hours:
            result = Signal(
                action="hold", size=0.0,
                reason=f"time_filter:kst_hour={hour_kst}_not_in_19h",
            )
            self._last_eval_bar_ts[symbol] = last_bar_ts
            self._last_eval_signal[symbol] = result
            return result

        close = history["close"]
        bb = signals.compute(
            "bollinger", close=close, window=self.BB_WINDOW, n_std=self.BB_STD,
        )
        upper = bb["upper"]
        if pd.isna(upper.iloc[-1]) or pd.isna(upper.iloc[-2]):
            result = Signal(action="hold", size=0.0, reason="bb_warmup")
            self._last_eval_bar_ts[symbol] = last_bar_ts
            self._last_eval_signal[symbol] = result
            return result

        short_fires, short_setup, short_trig = evaluate_short_fire_v11(
            history=history,
            bb_upper=upper,
            max_lookback=self.MAX_LOOKBACK,
            min_close_margin=self.min_close_margin,
            atr_period=self.atr_period,
            atr_body_mult=self.atr_body_mult,
            retrace_ratio=self.retrace_ratio,
        )

        c_now = float(close.iloc[-1])
        result: Signal | None
        if short_fires:
            bars_since = (
                len(history) - 1 - short_setup.breakout_index
                if short_setup is not None else -1
            )
            result = Signal(
                action="sell",
                size=self.default_size,
                reason=(
                    f"airborne_short_wl_fire:bo@-{bars_since},"
                    f"base={short_setup.base:.4f},ext={short_setup.extreme:.4f},"
                    f"trig={short_trig:.4f},c={c_now:.4f},"
                    f"r={self.retrace_ratio},kst={hour_kst}"
                ),
            )
            self._last_eval_bar_ts[symbol] = last_bar_ts
            # 2026-06-04 RIFUSDT 폭주 fix — fire 직후 cache 를 hold 로 덮어써서
            # 같은 봉 안에서의 중복 재발화 차단. 진입은 봉당 1회만.
            self._last_eval_signal[symbol] = Signal(
                action="hold", size=0.0,
                reason="airborne_short_wl_fired_this_bar",
            )
            # 데몬-발화 게이트 + 같은봉 dedup(영속) — 알림없는 매수·재시작 재매수
            # 차단 (#393/#392, KstHours 공유 헬퍼). closed_ts None(backtest)이면 무동작.
            return self._apply_daemon_gate_and_dedup(ctx, result, closed_ts)

        if short_setup is not None:
            result = Signal(
                action="hold", size=0.0,
                reason=(
                    f"airborne_short_wl_pending:"
                    f"trig={short_trig:.4f},c={c_now:.4f}"
                ),
            )
        else:
            result = Signal(action="hold", size=0.0, reason="no_active_short_setup")
        self._last_eval_bar_ts[symbol] = last_bar_ts
        self._last_eval_signal[symbol] = result
        return result
