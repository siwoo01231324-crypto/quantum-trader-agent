"""스윙 검색식 evaluator (#230).

A~G 만 (H 없음). 단타·5분대기 대비 임계값 차이:
- A: 900~9,000원 (2봉이내) — 더 좁고 직전봉도 평가
- B: 3% 이상 — 더 강함
- C: 50,000주 이상 — 더 강함
"""
from __future__ import annotations

from src.screeners.hts_cond.common import (
    DailyScreeningInputs,
    PROFILE_SWING,
    common_passes,
    evaluate_common,
)


class SwingCondition:
    """스윙 검색식 — A~G (H 없음)."""

    profile = PROFILE_SWING

    def passes(self, inputs: DailyScreeningInputs) -> bool:
        return common_passes(inputs, self.profile)

    def debug(self, inputs: DailyScreeningInputs) -> dict[str, bool]:
        return evaluate_common(inputs, self.profile)
