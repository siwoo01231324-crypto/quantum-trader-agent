---
type: signal
id: trendlines
name: Pivot-based Trendlines + Breakout Target
inputs: [close, high, low]
lookback: 5
tags: [technical, structure, pivot, breakout, support-resistance]
source: TradingView "[Trendlines]" indicator (2026-05-21 출력 역공학)
---

# Pivot-based Trendlines + Breakout Target

Fractal swing pivot 기반 trendline 자동 생성 + 돌파 시 1:1 길이 projection target. TradingView "[Trendlines]" indicator (502 line drawing + 62 horizontal level + 53 "Target" 라벨) 출력 패턴을 역공학.

## 계산

### 1. Pivot detection (N-bar fractal)

- `pivot_high[i] = True` iff `high[i] == max(high[i-N : i+N+1])` (N=lookback, default 5)
- `pivot_low[i] = True` iff `low[i] == min(low[i-N : i+N+1])`
- 첫·마지막 N bars 는 검증 불가능 → False

### 2. Trendline

가장 최근 두 pivot lows 잇기 → **상승 추세선** (uptrend line, p1 > p0 일 때만).
가장 최근 두 pivot highs 잇기 → **하락 추세선** (downtrend line, p1 < p0 일 때만).

선분 직선 방정식: `line(i) = p1 + slope × (i − i1)`, where `slope = (p1 − p0) / (i1 − i0)`.

### 3. Breakout signal

- `close > downtrend_line(i)` → `"breakout_up"` (저항 돌파)
- `close < uptrend_line(i)` → `"breakout_down"` (지지 이탈)

### 4. 1:1 Target projection

- breakout 시 amplitude = `|close − line(i)|`
- `target = close + amplitude` (up) 또는 `close − amplitude` (down)
- TradingView 의 "Target" 라벨 53건과 동일 산출.

### 5. Horizontal levels (S/R)

`compute_swing_levels(high, low, lookback, max_levels=60)` — 최근 pivot price 의 unique sorted list. TV horizontal_levels 62개 매칭.

## 특성

- **장점**: 가격 구조(pivot) 기반이라 ATR/MA 같은 통계 indicator 보다 시각적·직관적. 횡보장에서도 의미 있는 S/R 자동 식별.
- **단점**: lookback=5 가 너무 빡빡하면 noise 가 pivot 으로 잡힘. 너무 크면 pivot 부족. TF 별 최적 lookback 다름 (1d=5, 4h=8, 1h=12 권장 — 별도 sweep).
- **1:1 projection 한계**: fibonacci extension(1.618, 2.618 등) 보다 보수적. 강한 추세에서는 target 일찍 도달 → 추가 룰 필요.

## 사용

```python
from src.signals.trendlines import (
    compute_pivots,
    compute_swing_levels,
    compute_trendline_breakout,
    find_recent_trendlines,
)

pivots = compute_pivots(df["high"], df["low"], lookback=5)
levels = compute_swing_levels(df["high"], df["low"], lookback=5, max_levels=20)
breakout = compute_trendline_breakout(df["close"], df["high"], df["low"])
# breakout["signal"] == "breakout_up" 인 row 의 target_price 사용
```

`find_recent_trendlines(high, low, lookback, max_pairs=10)` — 시각화·디버깅용. TV 의 line drawing list 형태.

## 관련 노트

- [[13-feature-alpha-catalog]] — §3 Price Structure 카테고리
- [[donchian]] — rolling max/min 기반 (단순 breakout)
- [[airborne-bb-reversal]] — 다른 reversal/breakout 패턴
