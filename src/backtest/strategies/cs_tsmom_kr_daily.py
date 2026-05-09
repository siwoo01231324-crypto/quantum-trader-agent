"""Cross-sectional TSMOM 12-1 — KRX universe (#218).

KRX 시총 top-N 풀에서 12-1 month time-series momentum 점수 상위 N 종목을
주간 동일가중 보유.

본 모듈은 `scripts/bench_cs_tsmom_kr.py` 의 검증된 로직 (Sharpe 0.871, Ann
22.99%, MDD -42.99% / 5y / KOSPI200 + KOSDAQ150 → top-20) 을 importable
함수로 노출. AsyncStrategy wrap 은 후속 phase 에서 추가.

Score: `log(close[t-21] / close[t-252])` — 12개월 모멘텀 - 1개월 reversal.
Universe pin-date: 호출자가 universe 의 시점을 fix 한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.strategies._cs_helpers import build_weights, liquid_mask_panel


def score_panel(close: pd.DataFrame, long_lb: int = 252, skip_lb: int = 21
                ) -> pd.DataFrame:
    """t 시점 score = log(close[t-skip_lb] / close[t-long_lb])."""
    c_skip = close.shift(skip_lb)
    c_long = close.shift(long_lb)
    return np.log(c_skip / c_long)


def compute_weights(
    close: pd.DataFrame,
    turnover: pd.DataFrame,
    *,
    top_n: int = 20,
    rebal_freq: int = 5,
    long_lb: int = 252,
    skip_lb: int = 21,
    min_turnover: float = 1e9,
    min_price: float = 1000.0,
) -> pd.DataFrame:
    score = score_panel(close, long_lb=long_lb, skip_lb=skip_lb)
    eligible = liquid_mask_panel(turnover, close, min_turnover, min_price)
    return build_weights(
        close,
        score_fn=lambda i: score.iloc[i],
        top_n=top_n,
        rebal_freq=rebal_freq,
        warmup=long_lb,
        eligible_mask_fn=eligible,
        score_threshold=0.0,
    )
