"""Cross-sectional TSMOM 12-1 — Binance USDT spot universe (#218).

Binance 24h 거래량 top-N 풀에서 12-1 month TSMOM 상위 N 동일가중 주간 리밸.

본 모듈은 `scripts/bench_cs_tsmom_crypto.py` 의 검증된 로직 (Sharpe 1.328,
Ann 90.85%, MDD -52.42% / 5y / Binance top-30 → top-10) 을 importable
함수로 노출. KRX 버전 ([[cs_tsmom_kr_daily]]) 의 자산군 미러 — score 정의는
동일, 자산별 액세서리만 다름 (min_price 없음, quote_volume 사용).
"""
from __future__ import annotations

import pandas as pd

from backtest.strategies._cs_helpers import build_weights, liquid_mask_panel
from backtest.strategies.cs_tsmom_kr_daily import score_panel as _krx_score


def score_panel(close: pd.DataFrame, long_lb: int = 252, skip_lb: int = 21
                ) -> pd.DataFrame:
    """KRX 버전과 동일 score logic."""
    return _krx_score(close, long_lb=long_lb, skip_lb=skip_lb)


def compute_weights(
    close: pd.DataFrame,
    quote_volume: pd.DataFrame,
    *,
    top_n: int = 10,
    rebal_freq: int = 5,
    long_lb: int = 252,
    skip_lb: int = 21,
    min_quote_vol: float = 1e7,
) -> pd.DataFrame:
    score = score_panel(close, long_lb=long_lb, skip_lb=skip_lb)
    eligible = liquid_mask_panel(quote_volume, close, min_quote_vol, min_price=0.0)
    return build_weights(
        close,
        score_fn=lambda i: score.iloc[i],
        top_n=top_n,
        rebal_freq=rebal_freq,
        warmup=long_lb,
        eligible_mask_fn=eligible,
        score_threshold=0.0,
    )
