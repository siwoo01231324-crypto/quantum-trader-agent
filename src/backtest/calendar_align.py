"""교차 캘린더 얼라인먼트 헬퍼.

KRX 휴장일 0-fill 금지: ENB/ρ 왜곡 방지를 위해 index 교집합만 유지.
NaN-fill 또한 금지 — 결측 날짜는 무조건 drop.
"""
from __future__ import annotations

import pandas as pd


def intersect_trading_days(
    returns_by_strategy: dict[str, pd.Series],
) -> pd.DataFrame:
    """각 전략 일수익률 시계열의 index 교집합만 유지한 T×N DataFrame 반환.

    Args:
        returns_by_strategy: {strategy_id: daily_return_series} — index는 date or timestamp

    Returns:
        T×N DataFrame (columns = strategy_id 순서대로, index = 공통 거래일)
        교집합이 비어있으면 empty DataFrame 반환.

    Notes:
        - 0-fill / NaN-fill 금지. 모든 전략이 공통으로 존재하는 날짜만 유지.
        - compute_portfolio_risk_from_df 는 내부에서 dropna(how="any") 를 호출하므로
          이 함수로 사전 정렬하면 NaN 행 zero 기록.
    """
    if not returns_by_strategy:
        return pd.DataFrame()

    series_list = list(returns_by_strategy.values())
    common_index = series_list[0].index
    for s in series_list[1:]:
        common_index = common_index.intersection(s.index)

    if len(common_index) == 0:
        return pd.DataFrame(columns=list(returns_by_strategy.keys()))

    aligned = {
        strategy_id: series.loc[common_index]
        for strategy_id, series in returns_by_strategy.items()
    }
    return pd.DataFrame(aligned)
