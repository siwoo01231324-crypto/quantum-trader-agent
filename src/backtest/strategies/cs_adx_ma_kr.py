"""Cross-sectional 5/20 EMA cross + ADX(14) > 25 — KRX universe (#218).

Single-ticker `swing_kr_daily.swing_adx_ma` 의 universe-scan 변환본.

가설: 단기·장기 EMA 교차 + ADX 강세 (≥ 25) 가 동시에 있으면 추세 시작.
Cross-sectional 점수: EMA 차이 % + ADX 점수의 조합.

Score 정의:
    ema_gap = (ema_fast - ema_slow) / ema_slow         # > 0 이면 골든크로스 영역
    adx_norm = max(0, ADX(14) - threshold) / 100        # 임계값 초과분
    score = ema_gap * adx_norm
    EMA 골든크로스 아니면 0.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.strategies._cs_helpers import (
    adx_panel,
    build_weights,
    ema_panel,
    liquid_mask_panel,
)


def score_panel(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame,
                fast: int = 5, slow: int = 20, adx_period: int = 14,
                adx_threshold: float = 25.0) -> pd.DataFrame:
    ema_fast = ema_panel(close, fast)
    ema_slow = ema_panel(close, slow)
    ema_gap = (ema_fast - ema_slow) / ema_slow.replace(0, np.nan)
    ema_gap = ema_gap.clip(lower=0).fillna(0.0)  # 양수 (골든크로스 영역) 만
    adx = adx_panel(high, low, close, adx_period)
    adx_norm = ((adx - adx_threshold) / 100.0).clip(lower=0).fillna(0.0)
    return ema_gap * adx_norm


def compute_weights(
    high: pd.DataFrame,
    low: pd.DataFrame,
    close: pd.DataFrame,
    turnover: pd.DataFrame,
    *,
    top_n: int = 20,
    rebal_freq: int = 5,
    fast: int = 5,
    slow: int = 20,
    adx_period: int = 14,
    adx_threshold: float = 25.0,
    min_turnover: float = 1e9,
    min_price: float = 1000.0,
) -> pd.DataFrame:
    score = score_panel(high, low, close, fast, slow, adx_period, adx_threshold)
    # look-ahead 방지: rebal i 시점에서는 t-1 까지 정보만 — score.iloc[i] 사용 OK
    # (build_weights 가 weights.shift(1) 로 t+1 진입 처리)
    eligible = liquid_mask_panel(turnover, close, min_turnover, min_price)
    return build_weights(
        close,
        score_fn=lambda i: score.iloc[i],
        top_n=top_n,
        rebal_freq=rebal_freq,
        warmup=max(slow, adx_period * 2),
        eligible_mask_fn=eligible,
    )
