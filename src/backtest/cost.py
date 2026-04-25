"""공통 비용 헬퍼 — instrument_type 별 거래비용 차감.

Formula (per bar):
    daily_return[t] = raw_return[t]
        - cost_buy  * max(Δposition[t], 0)
        - cost_sell * max(-Δposition[t], 0)

Constants:
    COST_CRYPTO_PER_SIDE = 0.001   (0.10%, 편도)
    COST_KRX_BUY         = 0.00015 (0.015%)
    COST_KRX_SELL        = 0.00245 (0.245%: 거래세 0.23% + 수수료 0.015%)
"""
from __future__ import annotations

from typing import Literal

import pandas as pd

COST_CRYPTO_PER_SIDE: float = 0.001
COST_KRX_BUY: float = 0.00015
COST_KRX_SELL: float = 0.00245


def apply_cost(
    returns: pd.Series,
    positions: pd.Series,
    instrument_type: Literal["crypto", "krx"],
) -> pd.Series:
    """raw_return 시계열에 거래비용을 차감한 net return 시계열 반환.

    Args:
        returns:   raw daily-return series (index=date/timestamp)
        positions: position series (same index; values ∈ [0, 1] — fraction of equity)
        instrument_type: "crypto" or "krx"

    Returns:
        net return series (same index as returns)
    """
    delta = positions.diff().fillna(0.0)

    if instrument_type == "crypto":
        buy_cost = COST_CRYPTO_PER_SIDE * delta.clip(lower=0.0)
        sell_cost = COST_CRYPTO_PER_SIDE * (-delta).clip(lower=0.0)
    else:  # krx
        buy_cost = COST_KRX_BUY * delta.clip(lower=0.0)
        sell_cost = COST_KRX_SELL * (-delta).clip(lower=0.0)

    return returns - buy_cost - sell_cost
