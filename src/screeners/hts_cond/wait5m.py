"""5분대기작전 검색식 evaluator (#230).

A~G 공통 + H = "상승방향 정적 VI 근접율 ≤ 3.0%".
정적 VI 발동가 = 전일종가 × 1.10 (시가 결정 전·단순 계산. KRX 공식 정의:
시가 후엔 직전 단일가 × 1.10. 본 evaluator 는 단순 계산 사용).
근접율 = (VI_price - current) / VI_price.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.screeners.hts_cond.common import (
    DailyScreeningInputs,
    PROFILE_WAIT5M,
    common_passes,
)


@dataclass(frozen=True)
class Wait5mInputs:
    daily: DailyScreeningInputs
    current_price: float        # 평가 시점의 현재가 (intraday)


def cond_h_wait5m(
    prev_close: float,
    current_price: float,
    *,
    proximity_pct: float = 3.0,
    vi_multiplier: float = 1.10,
) -> bool:
    """5분대기 H: 상승방향 정적 VI 근접율 ≤ proximity_pct%.

    VI_price = prev_close × vi_multiplier (default 1.10 = +10%).
    근접율 = (VI_price - current_price) / VI_price.
    조건: 0 ≤ 근접율 ≤ proximity_pct/100.  즉 current ≤ VI 이면서 VI 의 proximity_pct% 이내.
    """
    if prev_close <= 0 or current_price <= 0:
        return False
    vi_price = prev_close * vi_multiplier
    proximity = (vi_price - current_price) / vi_price
    return 0.0 <= proximity <= proximity_pct / 100.0


class Wait5mCondition:
    """5분대기작전 검색식 — A~G + H (상승 정적 VI 근접율 3%)."""

    profile = PROFILE_WAIT5M

    def passes(self, inputs: Wait5mInputs) -> bool:
        if not common_passes(inputs.daily, self.profile):
            return False
        return cond_h_wait5m(inputs.daily.prev_close, inputs.current_price)

    def debug(self, inputs: Wait5mInputs) -> dict[str, bool]:
        from src.screeners.hts_cond.common import evaluate_common
        d = evaluate_common(inputs.daily, self.profile)
        d["H"] = cond_h_wait5m(inputs.daily.prev_close, inputs.current_price)
        return d
