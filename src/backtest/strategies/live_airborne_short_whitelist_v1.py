"""Live-scanner: SHORT-only airborne + 21-symbol whitelist + 19시간 게이트.

기존 ``LiveAirborneBbReversalKstHours`` (bidir, 4시간 게이트) 를 상속하고
**세 가지 차이** 만 적용:

  1. **kst_entry_hours**: 4 → 19 시간 (Hard OOS 검증된 train_PF>1 시간)
  2. **get_universe**: dynamic top-100 → ``config/airborne_short_whitelist.yaml``
     의 status=active 인 종목만 (현재 15종)
  3. **side filter**: LONG fire 는 hold 로 변환 — SHORT 만 발주
  4. **retrace_ratio**: 0.4 → 0.6 (Hard OOS 검증값, signals 모듈의 신규 kwarg)
  5. **atr_body_mult**: 0.6 → 0.3 (Hard OOS 검증값)

청산·warmup·BB·ATR 로직은 부모 100% 재사용. 데몬 없이 orchestrator 안에서
direct dispatch.

5y Hard OOS:
  test PF = 1.214, sumR = +1,395%, 5.45 trades/day
  vs legacy {8,11,16,22} 4-hour gate 게이트 te_PF=1.086 (알파 92% 손실)

Spec: ``docs/specs/strategies/live-airborne-short-whitelist-v1.md``
Whitelist: ``config/airborne_short_whitelist.yaml`` (weekly refresh)
"""
from __future__ import annotations

from pathlib import Path
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

# 19-hour gate — Hard OOS 의 train_PF>1 + n>=30 통과한 시간만.
# 제외: {4, 6, 7, 8, 13} (train PF<1). legacy 의 {8,11,16,22} 와 다름.
# 자세한 sweep 결과: ``scripts/airborne_short_whitelist_hour_sweep.py``.
_KST_HOURS_19: frozenset[int] = frozenset(
    {0, 1, 2, 3, 5, 9, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
)

# Hard OOS 검증값. 부모 default 0.4 보다 깊은 되돌림 요구.
_RETRACE_RATIO_HARD_OOS: float = 0.6
_ATR_BODY_MULT_HARD_OOS: float = 0.3

# Whitelist yaml 경로 — repo root 기준.
_WHITELIST_YAML: Path = (
    Path(__file__).resolve().parents[3] / "config" / "airborne_short_whitelist.yaml"
)

# 임포트 시점 (orchestrator 시작) 의 fallback universe — yaml 로드 실패 대비.
# Hard OOS active 15종.
_FALLBACK_UNIVERSE: tuple[str, ...] = (
    "1000SHIBUSDT", "AAVEUSDT", "APTUSDT", "ARBUSDT", "ATOMUSDT",
    "AXSUSDT", "DASHUSDT", "FETUSDT", "IDUSDT", "LTCUSDT",
    "RIFUSDT", "UNIUSDT", "XLMUSDT", "XRPUSDT", "ZECUSDT",
)


def _load_active_universe() -> list[str]:
    """yaml 로드 시도 → active 종목 정렬 list. 실패 시 fallback."""
    try:
        from src.live.airborne_short_whitelist.whitelist_loader import (
            active_symbols,
            load_whitelist,
        )
        cfg = load_whitelist(_WHITELIST_YAML)
        actives = sorted(active_symbols(cfg))
        if actives:
            return actives
    except Exception:  # noqa: BLE001
        pass
    return list(_FALLBACK_UNIVERSE)


class LiveAirborneShortWhitelistV1(LiveAirborneBbReversalKstHours):
    """SHORT-only airborne + 21-symbol whitelist + KST 19-hour 게이트.

    Hard OOS 검증 (train 2021-2023 / test 2024-2025):
        test PF = 1.214, sumR = +1,395%, 5.45 trades/day

    Spec: docs/specs/strategies/live-airborne-short-whitelist-v1.md
    """

    strategy_id: ClassVar[str] = "live-airborne-short-whitelist-v1"

    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06
    shorts_allowed: ClassVar[bool] = True  # 부모 True 명시 — sell intent reduce_only=False

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

    @classmethod
    def get_universe(cls) -> list[str]:
        """yaml 의 status=active 종목만. 호출 시점에 yaml 재로드 — weekly
        refresh 후 다음 ``get_universe()`` 호출부터 새 list 반영.
        """
        return _load_active_universe()

    @classmethod
    def get_interval(cls) -> str:
        return "1h"

    async def on_bar(self, ctx: object) -> Signal | None:
        """부모와 동일한 구조이지만 *short fire 만* 발주.

        매 tick 평가 대신 *봉 마감 1회만* evaluate → 102종 × BB/ATR pandas
        계산 폭주 차단. 같은 봉 안에서는 캐시된 결과 즉시 반환.
        """
        snap = ctx["market_snapshot"]  # type: ignore[index]
        history: pd.DataFrame | None = snap.get("history")
        if history is None or len(history) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="warmup")
        symbol = snap.get("symbol", "?")

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
            self._last_eval_signal[symbol] = result
            return result

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
