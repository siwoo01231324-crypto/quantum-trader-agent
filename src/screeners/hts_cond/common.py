"""A~G 공통 일간 조건 evaluator (#230).

3개 검색식 (단타·5분대기·스윙) 의 공통 일간 조건. A,B,C 임계값은 검색식
profile (PROFILE_DTS / PROFILE_WAIT5M / PROFILE_SWING) 별로 다름.
D,E,F,G 는 모두 동일.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DailyScreeningInputs:
    """검색식 평가 시점의 일간 데이터 스냅샷.

    봉 인덱스: "0봉전" = 평가 시점의 봉 (today, intraday 시점에는 현재가/누적값
    사용). "1봉전" = 전일 마감 봉, "2봉전" = 전전일 마감 봉.
    """
    symbol: str
    prev_close: float           # 1봉전 종가
    prev_close_2: float | None  # 2봉전 종가 (스윙 A 의 "2봉이내" 평가용)
    today_close: float          # 0봉전 종가 (당일 close 또는 intraday 현재가)
    today_volume: int           # 0봉전 거래량 (당일 누적)
    vol_5d_cumsum: int          # 5봉 누적 거래량
    power_ratio: float          # 체결강도 (KIS tday_rltv, 당일 누적)
    ma5: float                  # 5일 이평
    ma20: float                 # 20일 이평
    ma60: float                 # 60일 이평


@dataclass(frozen=True)
class ProfileThresholds:
    """검색식 profile 별 A,B,C 임계값."""
    a_price_min: float
    a_price_max: float
    a_window: int               # 1: today만, 2: today + 1봉전 (any-match)
    b_return_min: float
    b_return_max: float
    c_volume_min: int
    c_volume_max: int


PROFILE_DTS = ProfileThresholds(
    a_price_min=900, a_price_max=10_000, a_window=1,
    b_return_min=0.02, b_return_max=0.30,
    c_volume_min=40_000, c_volume_max=999_999_999,
)

PROFILE_WAIT5M = ProfileThresholds(
    a_price_min=900, a_price_max=10_000, a_window=1,
    b_return_min=0.02, b_return_max=0.30,
    c_volume_min=40_000, c_volume_max=999_999_999,
)

PROFILE_SWING = ProfileThresholds(
    a_price_min=900, a_price_max=9_000, a_window=2,
    b_return_min=0.03, b_return_max=0.30,
    c_volume_min=50_000, c_volume_max=999_999_999,
)


D_VOL_5D_MIN = 500_000
D_VOL_5D_MAX = 90_000_000_000
E_POWER_MIN = 90.0
E_POWER_MAX = 1_000.0
G_RETURN_MIN = 0.05
G_RETURN_MAX = 0.30


def cond_a(inputs: DailyScreeningInputs, prof: ProfileThresholds) -> bool:
    candidates = [inputs.today_close]
    if prof.a_window >= 2 and inputs.prev_close > 0:
        candidates.append(inputs.prev_close)
    return any(prof.a_price_min <= c <= prof.a_price_max for c in candidates)


def cond_b(inputs: DailyScreeningInputs, prof: ProfileThresholds) -> bool:
    if inputs.prev_close <= 0:
        return False
    ret = (inputs.today_close - inputs.prev_close) / inputs.prev_close
    return prof.b_return_min <= ret <= prof.b_return_max


def cond_c(inputs: DailyScreeningInputs, prof: ProfileThresholds) -> bool:
    return prof.c_volume_min <= inputs.today_volume <= prof.c_volume_max


def cond_d(inputs: DailyScreeningInputs) -> bool:
    return D_VOL_5D_MIN <= inputs.vol_5d_cumsum <= D_VOL_5D_MAX


def cond_e(inputs: DailyScreeningInputs) -> bool:
    return E_POWER_MIN <= inputs.power_ratio <= E_POWER_MAX


def cond_f(inputs: DailyScreeningInputs) -> bool:
    """주가-이동평균선 정배열: close > MA5 > MA20 > MA60 (strictly)."""
    return inputs.today_close > inputs.ma5 > inputs.ma20 > inputs.ma60


def cond_g(inputs: DailyScreeningInputs) -> bool:
    if inputs.prev_close <= 0:
        return False
    ret = (inputs.today_close - inputs.prev_close) / inputs.prev_close
    return G_RETURN_MIN <= ret <= G_RETURN_MAX


def evaluate_common(
    inputs: DailyScreeningInputs,
    prof: ProfileThresholds,
) -> dict[str, bool]:
    """A~G 각 조건의 통과 여부를 dict 로 반환 (디버깅·리포트용)."""
    return {
        "A": cond_a(inputs, prof),
        "B": cond_b(inputs, prof),
        "C": cond_c(inputs, prof),
        "D": cond_d(inputs),
        "E": cond_e(inputs),
        "F": cond_f(inputs),
        "G": cond_g(inputs),
    }


def common_passes(inputs: DailyScreeningInputs, prof: ProfileThresholds) -> bool:
    """A~G 모두 True 여야 통과."""
    return all(evaluate_common(inputs, prof).values())
