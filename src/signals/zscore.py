"""Rolling z-score factor in log-price domain."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .registry import register


@register("zscore", inputs=["close"], signal_type="mean_reversion", bar_interval="1h", alpha_horizon_bars=5, window=60)
def compute_zscore(
    close: pd.Series,
    window: int = 60,
) -> pd.Series:
    """Rolling z-score of log(close).

    z = (log(close) - rolling_mean(log(close), window)) / rolling_std(log(close), window)

    Returns raw z-value with no band scaling. First `window - 1` bars are NaN (warmup).
    Zero-std windows (constant price) return NaN.

    NOTE — pct_b vs zscore distinction (서로 대체 불가):
      pct_b 는 선형 가격 + band-scaled [0,1] 출력. zscore 는 log-가격 도메인 + band 구조 없는
      raw z-value. 서로 대체 불가.
    """
    log_close = np.log(close)
    rolling_mean = log_close.rolling(window).mean()
    rolling_std = log_close.rolling(window).std(ddof=1)
    return (log_close - rolling_mean) / rolling_std.where(rolling_std != 0, np.nan)
