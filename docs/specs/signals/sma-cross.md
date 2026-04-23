---
type: signal
id: sma-cross
name: SMA Crossover
inputs: [close]
lookback: 60
tags: [technical, trend]
---

# SMA Crossover

단순 이동평균(short SMA) 과 장기 SMA 가 교차하는 시점을 포착하는 추세 전환 시그널이다. 단기선이 장기선을 위로 뚫으면 `"golden"`, 아래로 뚫으면 `"dead"` 를 반환한다.

## 계산

1. `sma_short = close.rolling(short_window).mean()` (기본 20)
2. `sma_long  = close.rolling(long_window).mean()` (기본 60)
3. `diff = sma_short - sma_long`, `prev = diff.shift(1)`
4. `diff > 0 & prev <= 0` → golden cross
5. `diff < 0 & prev >= 0` → dead cross

`shift(1)` 을 내부에서 사용하므로 바 `N` 의 신호는 반드시 바 `N-1` 이하의 데이터만 쓴다.

## 관련 노트

- [[13-feature-alpha-catalog]] — §1.3 Trend 카테고리 (SMA/EMA + 골든/데드 크로스)
