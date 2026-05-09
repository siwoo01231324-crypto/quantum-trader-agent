"""Cross-sectional MACD bullish + 변동성 필터 — Binance USDT spot universe (#218).

Single-ticker `momo_vol_filtered` (BTC 4h MACD + realized_vol < 80%) 의
universe-scan 변환본.

가설: MACD 가 양수 모멘텀 + 변동성이 ceiling 미만인 종목만 안정적 추세.
Cross-sectional: 그런 종목 중 모멘텀 강도 (MACD-signal 차이) 가 가장 큰 N 보유.

Score:
    macd_strength = (MACD - signal_line) / abs(close)         # 정규화
    vol_pass = (realized_vol < vol_ceiling)
    score = macd_strength * vol_pass
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.strategies._cs_helpers import (
    build_weights,
    liquid_mask_panel,
    macd_panel,
)


def realized_vol_panel(close: pd.DataFrame, window: int = 30,
                       trading_days: int = 365) -> pd.DataFrame:
    """일수익률 표준편차 × sqrt(연환산)."""
    bar_ret = close.pct_change()
    return bar_ret.rolling(window, min_periods=window // 2).std() * np.sqrt(trading_days)


def score_panel(close: pd.DataFrame, macd_fast: int = 12, macd_slow: int = 26,
                macd_signal: int = 9, vol_window: int = 30,
                vol_ceiling: float = 0.80) -> pd.DataFrame:
    macd_line, sig_line, _ = macd_panel(close, macd_fast, macd_slow, macd_signal)
    macd_strength = ((macd_line - sig_line) / close.replace(0, np.nan)).clip(lower=0).fillna(0.0)
    vol = realized_vol_panel(close, vol_window)
    vol_pass = (vol < vol_ceiling).fillna(False).astype(float)
    return macd_strength * vol_pass


def compute_weights(
    close: pd.DataFrame,
    quote_volume: pd.DataFrame,
    *,
    top_n: int = 10,
    rebal_freq: int = 5,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    vol_window: int = 30,
    vol_ceiling: float = 0.80,
    min_quote_vol: float = 1e7,
) -> pd.DataFrame:
    score = score_panel(close, macd_fast, macd_slow, macd_signal, vol_window, vol_ceiling)
    eligible = liquid_mask_panel(quote_volume, close, min_quote_vol, min_price=0.0)
    return build_weights(
        close,
        score_fn=lambda i: score.iloc[i],
        top_n=top_n,
        rebal_freq=rebal_freq,
        warmup=max(macd_slow + macd_signal, vol_window),
        eligible_mask_fn=eligible,
    )
