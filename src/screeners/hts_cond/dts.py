"""단타 검색식 evaluator (#230).

A~G 공통 + H = "3분봉 10봉 이내, 종가 ≥ 20단순이평 (이격도 100~999%)".
이격도 = (close / SMA20) × 100. 999% 상단은 사실상 무제한이므로 실질 의미는
"종가가 20MA 이상". 키움 공식 이격도 정의 (kiwoom.com/wm/fnd/fs010/fndTechIndiGuidePop).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.screeners.hts_cond.common import (
    DailyScreeningInputs,
    PROFILE_DTS,
    common_passes,
)


@dataclass(frozen=True)
class ThreeMinBar:
    """3분봉 minimal view — 단타 H 평가용. close 만 필수."""
    close: float


@dataclass(frozen=True)
class DtsInputs:
    daily: DailyScreeningInputs
    three_min_bars: Sequence[ThreeMinBar]  # 최근 (ma_period + window) 이상


def cond_h_dts(
    three_min_bars: Sequence[ThreeMinBar],
    *,
    window: int = 10,
    ma_period: int = 20,
    disparity_min: float = 100.0,
    disparity_max: float = 999.0,
) -> bool:
    """단타 H: 최근 `window` 개 3분봉 중 1봉이라도 이격도가 [disparity_min, disparity_max] 범위.

    이격도 = (close / SMA20) × 100. SMA20 은 end_idx 포함 직전 20봉 (end_idx-19..end_idx).
    """
    closes = [b.close for b in three_min_bars]
    n = len(closes)
    if n < ma_period:
        return False
    start_idx = max(ma_period - 1, n - window)
    for i in range(start_idx, n):
        sma = sum(closes[i - ma_period + 1:i + 1]) / ma_period
        if sma <= 0:
            continue
        disparity_pct = closes[i] / sma * 100.0
        if disparity_min <= disparity_pct <= disparity_max:
            return True
    return False


class DtsCondition:
    """단타 검색식 — A~G + H (3분봉 20MA 지지)."""

    profile = PROFILE_DTS

    def passes(self, inputs: DtsInputs) -> bool:
        if not common_passes(inputs.daily, self.profile):
            return False
        return cond_h_dts(inputs.three_min_bars)

    def debug(self, inputs: DtsInputs) -> dict[str, bool]:
        from src.screeners.hts_cond.common import evaluate_common
        d = evaluate_common(inputs.daily, self.profile)
        d["H"] = cond_h_dts(inputs.three_min_bars)
        return d
