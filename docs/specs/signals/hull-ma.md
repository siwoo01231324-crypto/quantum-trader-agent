---
type: signal
id: hull-ma
name: Hull Moving Average (HMA) + Crossover
inputs: [close]
lookback: 60
tags: [technical, trend, hma, hull]
source: TradingView "HMA - 훌 이동평균선" indicator (2026-05-21 추출)
---

# Hull Moving Average (HMA) + Crossover

Alan Hull 의 1994 표준 공식. 일반 SMA/EMA 대비 lag 가 작으면서도 weighted MA 의 noise filtering 을 유지하는 trend MA.

```
HMA(n) = WMA( 2 × WMA(close, n/2) − WMA(close, n), √n )
```

TradingView "HMA - 훌 이동평균선" 인디케이터의 MHULL/SHULL 두 plot 을 재현. 보통 fast/slow 두 HMA 의 crossover 로 진입·청산 신호.

## 계산

1. `wma_half = WMA(close, length/2)` (linear weighted)
2. `wma_full = WMA(close, length)`
3. `raw = 2 × wma_half − wma_full`
4. `hma = WMA(raw, √length)`

Crossover:
- `hma_fast = HMA(close, fast)` (default 21)
- `hma_slow = HMA(close, slow)` (default 55)
- `diff = hma_fast − hma_slow`, `prev = diff.shift(1)`
- `diff > 0 & prev ≤ 0` → `"golden"` (long entry)
- `diff < 0 & prev ≥ 0` → `"dead"` (long exit)

`shift(1)` 으로 causality 보장.

## 특성

- **장점**: SMA(N) 대비 lag 가 약 50% 감소. 추세 전환 빠르게 포착.
- **단점**: 횡보장 whipsaw 잦음 — momentum-sensitive. 1m·5m·15m 봉에서는 false signal 가 비용 초과 가능. **1h 이상 권장**.
- **TF 권장**: 4h, 1d (TradingView 차트도 4h 로 사용 중).

## 관련 노트

- [[13-feature-alpha-catalog]] — §1.3 Trend 카테고리
- [[sma-cross]] — 동일 crossover 패턴 (SMA 버전)
- TradingView Pine source: `study("Hull Moving Average")` 표준
