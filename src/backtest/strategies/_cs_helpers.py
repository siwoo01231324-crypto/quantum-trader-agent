"""Cross-sectional 전략 공통 헬퍼 (#218).

이 모듈은 universe-scan 전략들이 공유하는 코드:
- 종목×시점 패널 위에서 동작하는 벡터화 기술 지표 (RSI, MACD, BB, ATR, ADX)
- Cross-sectional 랭킹 → top-N 선택 → equal-weight 가중치 시계열 생성
- 거래비용·turnover·portfolio 일수익률 계산

look-ahead 방지 원칙: 모든 지표는 t 시점 정보만으로 계산되어야 하며, 시그널을
t 시점에서 산출하면 t+1 진입 (returns = signal.shift(1) * price.pct_change()).

레퍼런스: `docs/specs/universe-scan-strategy-pattern.md`
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Panel-form technical indicators (vectorized over columns)
# ---------------------------------------------------------------------------

def rsi_panel(close: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Wilder RSI on panel. close shape = [date, ticker]."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.fillna(50.0)


def ema_panel(s: pd.DataFrame, span: int) -> pd.DataFrame:
    return s.ewm(span=span, adjust=False).mean()


def macd_panel(close: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
               ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (macd_line, signal_line, histogram) panels."""
    macd = ema_panel(close, fast) - ema_panel(close, slow)
    sig = ema_panel(macd, signal)
    hist = macd - sig
    return macd, sig, hist


def bollinger_panel(close: pd.DataFrame, period: int = 20, std_mult: float = 2.0
                    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (mid, upper, lower) panels."""
    mid = close.rolling(period, min_periods=period).mean()
    sd = close.rolling(period, min_periods=period).std()
    return mid, mid + std_mult * sd, mid - std_mult * sd


def atr_panel(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame,
              period: int = 14) -> pd.DataFrame:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3]).groupby(level=0).max() if False else \
         pd.DataFrame(np.maximum.reduce([tr1.values, tr2.values, tr3.values]),
                      index=close.index, columns=close.columns)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def adx_panel(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame,
              period: int = 14) -> pd.DataFrame:
    """Wilder ADX on panel. Returns ADX line in [0, 100]."""
    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr = atr_panel(high, low, close, period)
    plus_di = 100 * plus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    return adx.fillna(0.0)


# ---------------------------------------------------------------------------
# Cross-sectional weight builder
# ---------------------------------------------------------------------------

ScoreFn = Callable[[int], pd.Series]
"""Score function: index i (rebal row) → Series[ticker → score]. NaN/neg = ineligible."""


def build_weights(
    closes: pd.DataFrame,
    score_fn: ScoreFn,
    top_n: int,
    rebal_freq: int,
    warmup: int,
    *,
    eligible_mask_fn: Callable[[int], pd.Series] | None = None,
    score_threshold: float = 0.0,
) -> pd.DataFrame:
    """Cross-sectional rebal 가중치 시계열을 생성.

    Args:
        closes: [date, ticker] 종가 패널.
        score_fn: 각 rebal 시점 i 에서 종목별 score 반환. NaN 이거나
                  ≤ score_threshold 이면 ineligible.
        top_n: 한 번에 보유할 최대 종목수.
        rebal_freq: 리밸 주기 (bar 수, 예: 5 = 주간).
        warmup: 첫 rebal 까지 필요한 최소 bar 수 (지표 lookback).
        eligible_mask_fn: 추가 필터 (유동성·가격 등). i → bool Series.
        score_threshold: 이 값 초과인 score 만 picks 후보 (default 0.0 → long-only).

    Returns:
        weights: [date, ticker] DataFrame, 행마다 0~1 가중치 합 ≤ 1.
    """
    n = len(closes)
    weights = pd.DataFrame(np.nan, index=closes.index, columns=closes.columns)
    for i in range(warmup, n, rebal_freq):
        score = score_fn(i)
        if eligible_mask_fn is not None:
            score = score.where(eligible_mask_fn(i), other=np.nan)
        eligible = score[score.notna() & (score > score_threshold)]
        row = pd.Series(0.0, index=closes.columns)
        if not eligible.empty:
            picks = eligible.nlargest(top_n).index
            row.loc[picks] = 1.0 / len(picks)
        weights.iloc[i] = row
    weights = weights.ffill().fillna(0.0)
    return weights


# ---------------------------------------------------------------------------
# Backtest helpers (parallel to scripts/bench_cs_tsmom_kr.py shape)
# ---------------------------------------------------------------------------

def daily_returns_from_weights(weights: pd.DataFrame, closes: pd.DataFrame,
                               cost_bps: float) -> pd.Series:
    """t 시점 weights 를 (t-1) 가중치로 받아 (t-1→t) 수익 계산. 비용 차감.

    cost_bps: 라운드트립 (entry+exit) 합산 bps. turnover × cost_bps/2/10000 차감.
    """
    bar_ret = closes.pct_change().fillna(0.0)
    pos_y = weights.shift(1).fillna(0.0)
    gross = (pos_y * bar_ret).sum(axis=1)
    turnover = (weights - weights.shift(1).fillna(0.0)).abs().sum(axis=1)
    cost = turnover * (cost_bps / 10_000.0) / 2.0
    return gross - cost


def liquid_mask_panel(turnover: pd.DataFrame, close: pd.DataFrame,
                     min_turnover: float, min_price: float, window: int = 60
                     ) -> Callable[[int], pd.Series]:
    """유동성 + 가격 필터 → eligible_mask_fn 호환 클로저 반환.

    Note: PyYAML 1.1 spec 이 `1.0e9` 같은 표기를 str 로 파싱 → comparison TypeError.
    production.yaml 운영 디버깅 (2026-05-10) 후 방어용 float 캐스팅 추가.
    """
    min_turnover = float(min_turnover)
    min_price = float(min_price)
    avg_t = turnover.rolling(window, min_periods=window // 2).mean()

    def mask(i: int) -> pd.Series:
        return (avg_t.iloc[i] >= min_turnover) & (close.iloc[i] >= min_price)
    return mask
