"""Cross-sectional Bollinger Band 하단 반등 + MACD bullish cross — KRX universe.

Single-ticker `swing_kr_daily.swing_bb_macd` 의 universe-scan 변환본 (#218).

가설: BB 하단 이탈 후 회복 + MACD bullish 교차가 동시에 있는 종목이 강한
mean-reversion-then-trend 신호. universe 차원에서 score 가 가장 큰 N 종목 보유.

Score 정의:
    bb_recovery = (close - lower_band) / (mid - lower_band)  # 0~1, 1 이면 mid 도달
    macd_strength = MACD - signal_line                       # 양수일수록 bullish
    bb_below_mask = close.shift(2) < lower.shift(1)         # 최근 BB 하단 이탈 흔적
    score = bb_recovery * macd_strength * bb_below_mask
    음수 macd_strength 또는 BB 하단 이탈 흔적 없으면 0.

look-ahead 방지: shift(1) 적용된 lower 와 shift(2) close 사용.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.strategies._cs_helpers import (
    bollinger_panel,
    build_weights,
    liquid_mask_panel,
    macd_panel,
)


def score_panel(close: pd.DataFrame, bb_period: int = 20, bb_std: float = 2.0,
                macd_fast: int = 12, macd_slow: int = 26, macd_signal: int = 9,
                lookback_bars: int = 5) -> pd.DataFrame:
    mid, upper, lower = bollinger_panel(close, bb_period, bb_std)
    macd_line, sig_line, _ = macd_panel(close, macd_fast, macd_slow, macd_signal)

    # BB 하단 이탈 흔적: 최근 lookback_bars 안에 close < lower 적이 있나
    breached = ((close < lower).shift(1)
                .rolling(lookback_bars, min_periods=1).max()).fillna(0).astype(bool)
    # BB 회복도: lower → mid 사이 위치를 0~1 로 정규화
    bb_recovery = (close - lower) / (mid - lower).replace(0, np.nan)
    bb_recovery = bb_recovery.clip(lower=0, upper=1).fillna(0.0)
    # MACD strength: bullish 교차 후 양수 영역
    macd_strength = (macd_line - sig_line).clip(lower=0)
    score = bb_recovery * macd_strength * breached.astype(float)
    return score


def compute_weights(
    close: pd.DataFrame,
    turnover: pd.DataFrame,
    *,
    top_n: int = 20,
    rebal_freq: int = 5,
    bb_period: int = 20,
    bb_std: float = 2.0,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    lookback_bars: int = 5,
    min_turnover: float = 1e9,
    min_price: float = 1000.0,
) -> pd.DataFrame:
    score = score_panel(close, bb_period, bb_std, macd_fast, macd_slow, macd_signal,
                        lookback_bars)
    eligible = liquid_mask_panel(turnover, close, min_turnover, min_price)
    return build_weights(
        close,
        score_fn=lambda i: score.iloc[i],
        top_n=top_n,
        rebal_freq=rebal_freq,
        warmup=max(bb_period, macd_slow + macd_signal),
        eligible_mask_fn=eligible,
    )
