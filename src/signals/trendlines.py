"""Pivot-based trendlines + breakout target projection (2026-05-21).

TradingView "[Trendlines]" indicator 의 502 line / 62 horizontal level /
53 "Target" 라벨을 우리 시스템에서 재현. 출력 패턴 (line.y1/y2/x1/x2 +
horizontal_levels + Target 라벨) 으로 알고리즘 역공학:

1. **Pivot detection** — N-bar 좌우 lookback 으로 swing high/low 식별.
   `high[i]` 가 `high[i-N .. i+N]` 의 max → pivot high. low 도 동일.
2. **Trendline** — 인접한 두 pivot lows (uptrend) 또는 pivot highs (downtrend)
   를 잇는 선분.
3. **Breakout target projection** — 가격이 trendline 을 돌파한 bar 에서
   "추세선 시작 ~ 돌파 시점" 의 가격 진폭을 동일하게 돌파 방향으로 projection
   → target price. TradingView 의 "Target" 라벨 53건이 이 산출 결과.
4. **Horizontal levels** — 최근 N 개 pivot price 의 unique sorted list →
   S/R 레벨 차트 (TV 의 horizontal_levels 62개와 매칭).

본 모듈은 3 가지 인터페이스:
- ``compute_pivots(high, low, lookback)`` — boolean DataFrame (pivot_high, pivot_low)
- ``compute_swing_levels(high, low, lookback, max_levels)`` — list[float] (S/R)
- ``compute_trendline_breakout(close, high, low, lookback)`` — DataFrame(signal, target_price)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .registry import register


@register("pivots", inputs=["high", "low"], alpha_horizon_bars=20,
          bar_interval="1d", signal_type="breakout", lookback=5)
def compute_pivots(
    high: pd.Series,
    low: pd.Series,
    lookback: int = 5,
) -> pd.DataFrame:
    """Fractal pivot detection — N-bar 좌우 lookback.

    Args:
        high: 고가 시계열.
        low: 저가 시계열.
        lookback: 좌우 검증 bar 수 (default 5 → 5-bar fractal).

    Returns:
        DataFrame(pivot_high, pivot_low) — boolean. 첫·마지막 ``lookback`` bars
        는 검증 불가능 → False.

    Notes:
        한 봉이 동시에 pivot high 와 pivot low 일 수도 있음 (drastic intraday
        wick). 매우 드물어서 통상 무시.
    """
    if lookback < 1:
        raise ValueError(f"lookback must be >= 1, got {lookback}")
    n = len(high)
    pivot_h = pd.Series(False, index=high.index)
    pivot_l = pd.Series(False, index=low.index)
    h = high.to_numpy()
    l = low.to_numpy()
    for i in range(lookback, n - lookback):
        win_h = h[i - lookback : i + lookback + 1]
        win_l = l[i - lookback : i + lookback + 1]
        if not np.isnan(win_h).any() and h[i] == win_h.max():
            pivot_h.iloc[i] = True
        if not np.isnan(win_l).any() and l[i] == win_l.min():
            pivot_l.iloc[i] = True
    return pd.DataFrame({"pivot_high": pivot_h, "pivot_low": pivot_l},
                         index=high.index)


def compute_swing_levels(
    high: pd.Series,
    low: pd.Series,
    lookback: int = 5,
    max_levels: int = 60,
) -> list[float]:
    """Pivot price 의 unique sorted list (S/R 레벨 차트).

    TradingView [Trendlines] 의 horizontal_levels 62개와 매칭. 최근
    pivot 부터 max_levels 개수 까지 수집 → sorted desc.

    Returns: 가격 list (큰 값부터).
    """
    pivots = compute_pivots(high, low, lookback)
    prices: list[float] = []
    # 최신 pivot 부터 거꾸로 — recency 가 의미 있음
    for i in range(len(high) - 1, -1, -1):
        if pivots["pivot_high"].iloc[i]:
            prices.append(float(high.iloc[i]))
        if pivots["pivot_low"].iloc[i]:
            prices.append(float(low.iloc[i]))
        if len(prices) >= max_levels * 2:  # 양쪽 dedup 여유
            break
    uniq = sorted(set(round(p, 4) for p in prices), reverse=True)
    return uniq[:max_levels]


@dataclass(frozen=True, slots=True)
class TrendlineBreakout:
    """단일 breakout event — TradingView 의 'Target' 라벨 1건과 매칭."""
    direction: str           # "up" | "down"
    breakout_bar: int        # 돌파 발생 bar index
    breakout_price: float    # 돌파 시점 종가
    target_price: float      # 1:1 projection target
    line_start_bar: int      # trendline 시작 pivot bar
    line_start_price: float  # trendline 시작 가격


@register("trendline_breakout", inputs=["close", "high", "low"],
          alpha_horizon_bars=20, bar_interval="1d",
          signal_type="breakout", lookback=5)
def compute_trendline_breakout(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    lookback: int = 5,
) -> pd.DataFrame:
    """Pivot 기반 trendline breakout 신호 + 1:1 projection target.

    매 bar 마다 가장 최근 두 pivot lows (uptrend line) 와 pivot highs
    (downtrend line) 를 잇는 trendline 을 계산. 종가가 그 trendline 을
    돌파하는 bar 를 신호로 emit.

    Target = "추세선 시작 pivot 가격 ~ 돌파 시점 종가" 의 차이를 돌파 방향
    으로 다시 projection. 즉 1:1 길이 측정 이동.

    Returns:
        DataFrame(signal, target_price). signal: "breakout_up" / "breakout_down"
        / None. target_price: 매칭 시 projection 가격, 아니면 NaN.
    """
    if lookback < 1:
        raise ValueError(f"lookback must be >= 1, got {lookback}")
    pivots = compute_pivots(high, low, lookback)
    n = len(close)
    sig = pd.Series([None] * n, index=close.index, dtype=object)
    tgt = pd.Series(np.nan, index=close.index, dtype=float)

    # 가장 최근 2개의 pivot high/low 인덱스 추적
    recent_highs: list[int] = []
    recent_lows: list[int] = []
    for i in range(n):
        if pivots["pivot_high"].iloc[i]:
            recent_highs.append(i)
            if len(recent_highs) > 2:
                recent_highs.pop(0)
        if pivots["pivot_low"].iloc[i]:
            recent_lows.append(i)
            if len(recent_lows) > 2:
                recent_lows.pop(0)

        c_now = close.iloc[i]
        if pd.isna(c_now):
            continue

        # uptrend line: 두 pivot lows 잇기 → close 가 그 선 아래로 내려가면
        # breakout_down.
        if len(recent_lows) == 2:
            i0, i1 = recent_lows
            p0 = float(low.iloc[i0])
            p1 = float(low.iloc[i1])
            if i1 > i0 and p1 > p0:  # 상승 추세선
                slope = (p1 - p0) / (i1 - i0)
                line_now = p1 + slope * (i - i1)
                if c_now < line_now and not pd.isna(line_now):
                    sig.iloc[i] = "breakout_down"
                    amplitude = max(line_now - c_now, 0.0)
                    tgt.iloc[i] = c_now - amplitude  # 1:1 down

        # downtrend line: 두 pivot highs 잇기 → close 가 그 선 위로 올라가면
        # breakout_up.
        if len(recent_highs) == 2:
            i0, i1 = recent_highs
            p0 = float(high.iloc[i0])
            p1 = float(high.iloc[i1])
            if i1 > i0 and p1 < p0:  # 하락 추세선
                slope = (p1 - p0) / (i1 - i0)
                line_now = p1 + slope * (i - i1)
                if c_now > line_now and not pd.isna(line_now):
                    # downtrend 와 uptrend 가 동시에 trigger 될 일은 거의 없음
                    # — 동시이면 마지막 쓴 값 (이 case 는 breakout_up) 우선.
                    sig.iloc[i] = "breakout_up"
                    amplitude = max(c_now - line_now, 0.0)
                    tgt.iloc[i] = c_now + amplitude  # 1:1 up

    return pd.DataFrame({"signal": sig, "target_price": tgt},
                         index=close.index)


def find_recent_trendlines(
    high: pd.Series,
    low: pd.Series,
    lookback: int = 5,
    max_pairs: int = 10,
) -> list[dict]:
    """디버깅·시각화용 — 가장 최근의 trendline pair list 반환.

    TradingView 의 502 line 출력에 가까운 형태. 각 element:
    ``{"type": "up"|"down", "x1": int, "y1": float, "x2": int, "y2": float}``.
    """
    pivots = compute_pivots(high, low, lookback)
    out: list[dict] = []
    highs = [i for i in range(len(high)) if pivots["pivot_high"].iloc[i]]
    lows = [i for i in range(len(low)) if pivots["pivot_low"].iloc[i]]

    # 연속한 두 lows / highs 페어 만들기
    for i in range(len(lows) - 1, 0, -1):
        i0, i1 = lows[i - 1], lows[i]
        p0 = float(low.iloc[i0])
        p1 = float(low.iloc[i1])
        if p1 > p0:
            out.append({"type": "up", "x1": i0, "y1": p0, "x2": i1, "y2": p1})
        if len(out) >= max_pairs:
            break
    for i in range(len(highs) - 1, 0, -1):
        i0, i1 = highs[i - 1], highs[i]
        p0 = float(high.iloc[i0])
        p1 = float(high.iloc[i1])
        if p1 < p0:
            out.append({"type": "down", "x1": i0, "y1": p0, "x2": i1, "y2": p1})
        if len(out) >= max_pairs * 2:
            break
    return out
