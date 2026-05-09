"""Cross-sectional RSI bullish divergence — KRX universe (#218).

Single-ticker `momo_kis_v1` (RSI 다이버전스 진입) 의 universe-scan 변환본.

핵심 가설: 단일 종목에서 RSI 강세 다이버전스 (가격은 신저점, RSI 는 더
낮아지지 않음) 가 매수 신호인데, universe 차원에서는 "어느 종목이 가장 강한
RSI 강세 다이버전스 상태인가" 를 cross-sectional 로 점수화 → 상위 N 보유.

Score 정의:
    score = (RSI_now - RSI_at_recent_low) / (price_at_recent_low / price_now - 1 + 1e-6)
    where recent_low = rolling 20-bar 최저가 발생 바.
    값이 클수록 "가격은 떨어졌는데 RSI 는 더 떨어지지 않았다" = 강세 다이버전스.

이 정의는 단순하지만 cross-sectional 비교 가능하고 vectorized 됨.

Universe-scan 패턴 spec: `docs/specs/universe-scan-strategy-pattern.md`
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.strategies._cs_helpers import (
    build_weights,
    liquid_mask_panel,
    rsi_panel,
)


def score_panel(close: pd.DataFrame, rsi_period: int = 14, lookback: int = 20
                ) -> pd.DataFrame:
    """t 시점에서 종목별 RSI 강세 다이버전스 점수 패널 [date, ticker]."""
    rsi = rsi_panel(close, rsi_period)
    # 직전 lookback 바 동안의 최저 종가 발생 인덱스 + 그 시점 RSI
    low_idx = close.shift(1).rolling(lookback, min_periods=lookback).apply(
        lambda x: x.values.argmin() if len(x.dropna()) else np.nan, raw=False
    )
    # 위치 기반 lookup 은 효율 위해 vectorize: rolling.min/argmin proxy
    low_close = close.shift(1).rolling(lookback, min_periods=lookback).min()
    # RSI at recent low: 최근 20바 RSI 최저값으로 proxy (정확한 argmin 매칭 대신)
    low_rsi = rsi.shift(1).rolling(lookback, min_periods=lookback).min()
    # bullish divergence: 현재 RSI - 최근 RSI 최저 > 0 AND 현재 close <= 최근 close 최저 근처
    rsi_diff = rsi - low_rsi
    price_drop = (low_close / close.shift(1) - 1).clip(lower=0)
    score = rsi_diff * (1 + price_drop)  # 클수록 다이버전스 강함
    return score


def compute_weights(
    close: pd.DataFrame,
    turnover: pd.DataFrame,
    *,
    top_n: int = 20,
    rebal_freq: int = 5,
    rsi_period: int = 14,
    lookback: int = 20,
    min_turnover: float = 1e9,
    min_price: float = 1000.0,
) -> pd.DataFrame:
    score = score_panel(close, rsi_period=rsi_period, lookback=lookback)
    eligible = liquid_mask_panel(turnover, close, min_turnover, min_price)
    return build_weights(
        close,
        score_fn=lambda i: score.iloc[i],
        top_n=top_n,
        rebal_freq=rebal_freq,
        warmup=lookback + rsi_period,
        eligible_mask_fn=eligible,
        score_threshold=0.0,
    )
