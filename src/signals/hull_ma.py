"""Hull Moving Average (HMA) + fast/slow crossover signal (2026-05-21).

TradingView "HMA - 훌 이동평균선" 의 MHULL / SHULL 두 plot 을 우리 시스템에서
재현. Alan Hull 의 1994 표준 공식:

    HMA(n) = WMA(2 * WMA(close, n/2) - WMA(close, n), sqrt(n))

특성:
- 일반 SMA/EMA 대비 lag 가 크게 줄어듦 — 트렌드 전환 빠르게 포착
- 동시에 noise filtering 은 유지 (WMA 의 가중 평균)
- 단점: 추세 없는 횡보장에서 whipsaw 잦음 (HMA 자체가 momentum-sensitive)

본 모듈은 두 가지 인터페이스 제공:
- ``compute_hull_ma(close, length)`` — 단일 length HMA
- ``compute_hull_cross(close, fast=21, slow=55)`` — fast/slow crossover 신호
  (golden/dead). TradingView 의 MHULL/SHULL crossover 패턴과 동일 의미.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .registry import register


def _wma(series: pd.Series, length: int) -> pd.Series:
    """Linearly weighted moving average — 최근 bar 에 가장 큰 가중치."""
    if length <= 0:
        raise ValueError(f"length must be > 0, got {length}")
    weights = np.arange(1, length + 1, dtype=float)
    weights_sum = weights.sum()

    def _w(window: np.ndarray) -> float:
        return float(np.dot(window, weights) / weights_sum)

    return series.rolling(length).apply(_w, raw=True)


@register("hull_ma", inputs=["close"], alpha_horizon_bars=10,
          bar_interval="1d", signal_type="trend", length=55)
def compute_hull_ma(close: pd.Series, length: int = 55) -> pd.Series:
    """Hull Moving Average.

    Args:
        close: 종가 시계열.
        length: HMA period (default 55 — TradingView "HMA - 훌 이동평균선" 의
            일반적 slow HMA 값). 일반 fast HMA 는 21.

    Returns:
        HMA 시계열. 첫 ``length + sqrt(length) - 1`` bars 는 NaN (warmup).
    """
    if length < 2:
        raise ValueError(f"length must be >= 2, got {length}")
    half = max(1, length // 2)
    sqrt_n = max(1, int(np.sqrt(length)))
    wma_half = _wma(close, half)
    wma_full = _wma(close, length)
    raw = 2.0 * wma_half - wma_full
    return _wma(raw, sqrt_n)


@register("hull_cross", inputs=["close"], alpha_horizon_bars=15,
          bar_interval="1d", signal_type="trend", fast=21, slow=55)
def compute_hull_cross(
    close: pd.Series,
    fast: int = 21,
    slow: int = 55,
) -> pd.DataFrame:
    """Fast/slow HMA crossover signal — TradingView MHULL/SHULL 패턴.

    ``"golden"`` 은 fast HMA 가 slow HMA 를 상향 돌파하는 bar (long entry),
    ``"dead"`` 는 하향 돌파하는 bar (long exit / short entry).
    Returns a DataFrame with columns ``hma_fast``, ``hma_slow``, ``signal``.

    Default 21/55 는 TradingView 커뮤니티에서 가장 흔히 쓰는 페어. 4시간봉
    이상 권장 — 짧은 봉(1m/5m) 에서는 whipsaw 가 잦아 expectancy 음수.
    """
    if fast >= slow:
        raise ValueError(f"fast ({fast}) must be < slow ({slow})")

    hma_fast = compute_hull_ma(close, fast)
    hma_slow = compute_hull_ma(close, slow)
    diff = hma_fast - hma_slow
    prev = diff.shift(1)
    signal = pd.Series([None] * len(close), index=close.index, dtype=object)
    signal[(prev <= 0) & (diff > 0)] = "golden"
    signal[(prev >= 0) & (diff < 0)] = "dead"
    return pd.DataFrame(
        {"hma_fast": hma_fast, "hma_slow": hma_slow, "signal": signal},
        index=close.index,
    )
