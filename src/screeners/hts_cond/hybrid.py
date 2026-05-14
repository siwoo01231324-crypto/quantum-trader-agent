"""3개 HTS 검색식 (DTS, WAIT5M, SWING) OR 합성 evaluator (#230).

`evaluate_hybrid_or` 는 live-scanner strategy (`src/backtest/strategies/live_hts_hybrid.py`)
와 백테스트 도구 (`scripts/run_hts_cond_pilot.py`, `grid_hts_cond.py`) 양쪽에서 공통 사용.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.screeners.hts_cond.common import DailyScreeningInputs
from src.screeners.hts_cond.dts import DtsCondition, DtsInputs, ThreeMinBar
from src.screeners.hts_cond.swing import SwingCondition
from src.screeners.hts_cond.wait5m import Wait5mCondition, Wait5mInputs


@dataclass
class HybridEvalResult:
    passes: bool
    triggered_by: str  # "dts" | "wait5m" | "swing" | ""
    detail: dict


def evaluate_hybrid_or(
    daily_inputs: DailyScreeningInputs,
    bars_3m: Sequence[ThreeMinBar] | None,
    current_price: float,
) -> HybridEvalResult:
    """3개 검색식 OR 합성 평가. 1개라도 통과하면 True.

    Parameters
    ----------
    daily_inputs: 일간 조건 (A~G) 평가용 스냅샷
    bars_3m: DTS H (3분봉 20MA 지지) 평가용. None 이면 DTS 자동 fail
    current_price: WAIT5M H (정적 VI 근접율) 평가용

    Returns
    -------
    HybridEvalResult — 어느 검색식에서 fire 했는지 명시.
    """
    dts_pass = False
    if bars_3m is not None and len(bars_3m) >= 20:
        dts_pass = DtsCondition().passes(DtsInputs(daily=daily_inputs, three_min_bars=bars_3m))
    wait5m_pass = Wait5mCondition().passes(
        Wait5mInputs(daily=daily_inputs, current_price=current_price)
    )
    swing_pass = SwingCondition().passes(daily_inputs)

    detail = {"dts": dts_pass, "wait5m": wait5m_pass, "swing": swing_pass}
    if dts_pass:
        return HybridEvalResult(True, "dts", detail)
    if wait5m_pass:
        return HybridEvalResult(True, "wait5m", detail)
    if swing_pass:
        return HybridEvalResult(True, "swing", detail)
    return HybridEvalResult(False, "", detail)
