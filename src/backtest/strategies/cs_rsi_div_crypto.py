"""Cross-sectional RSI bullish divergence — Binance USDT spot universe (#218).

Single-ticker `momo_btc_v2` (BTC RSI 다이버전스) 의 universe-scan 변환본.
KRX 버전 ([[cs-rsi-div-kr]]) 의 자산군 미러: 24h volume 필터, 가격 하한 없음.
"""
from __future__ import annotations

import pandas as pd

from backtest.strategies._cs_helpers import build_weights, liquid_mask_panel
from backtest.strategies.cs_rsi_div_kr import score_panel as _krx_score


def score_panel(close: pd.DataFrame, rsi_period: int = 14, lookback: int = 20
                ) -> pd.DataFrame:
    """KRX 버전과 동일 score logic. 자산군 차이 없음 (RSI 는 가격 단위 무관)."""
    return _krx_score(close, rsi_period=rsi_period, lookback=lookback)


def compute_weights(
    close: pd.DataFrame,
    quote_volume: pd.DataFrame,
    *,
    top_n: int = 10,
    rebal_freq: int = 5,
    rsi_period: int = 14,
    lookback: int = 20,
    min_quote_vol: float = 1e7,
) -> pd.DataFrame:
    score = score_panel(close, rsi_period=rsi_period, lookback=lookback)
    # 크립토는 가격 하한 없음 (1 USDT 미만 알트 정상)
    eligible = liquid_mask_panel(quote_volume, close, min_quote_vol, min_price=0.0)
    return build_weights(
        close,
        score_fn=lambda i: score.iloc[i],
        top_n=top_n,
        rebal_freq=rebal_freq,
        warmup=lookback + rsi_period,
        eligible_mask_fn=eligible,
        score_threshold=0.0,
    )
