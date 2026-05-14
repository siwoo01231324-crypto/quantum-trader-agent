"""HTS 조건검색식 evaluator (#230).

3개 검색식 evaluator 를 export:
- `DtsCondition` (단타) — A~G + H (3분봉 20MA 지지)
- `Wait5mCondition` (5분대기작전) — A~G + H (상승 정적 VI 근접율 3%)
- `SwingCondition` (스윙) — A~G (H 없음, 일부 임계값 차이)
"""
from src.screeners.hts_cond.common import (
    DailyScreeningInputs,
    ProfileThresholds,
    PROFILE_DTS,
    PROFILE_WAIT5M,
    PROFILE_SWING,
    common_passes,
    evaluate_common,
)
from src.screeners.hts_cond.dts import DtsCondition, DtsInputs, cond_h_dts
from src.screeners.hts_cond.hybrid import HybridEvalResult, evaluate_hybrid_or
from src.screeners.hts_cond.swing import SwingCondition
from src.screeners.hts_cond.wait5m import Wait5mCondition, Wait5mInputs, cond_h_wait5m

__all__ = [
    "DailyScreeningInputs",
    "ProfileThresholds",
    "PROFILE_DTS",
    "PROFILE_WAIT5M",
    "PROFILE_SWING",
    "common_passes",
    "evaluate_common",
    "DtsCondition",
    "DtsInputs",
    "cond_h_dts",
    "Wait5mCondition",
    "Wait5mInputs",
    "cond_h_wait5m",
    "SwingCondition",
    "HybridEvalResult",
    "evaluate_hybrid_or",
]
