---
type: signal
id: rsi-divergence
name: RSI Divergence
inputs: [close, volume]
lookback: 14
source_model: null
tags: [technical, oscillator]
---

# RSI Divergence

가격과 RSI(14) 의 다이버전스를 탐지하는 기술적 신호. 주로 [[momo-btc-v2]] 에서 진입·청산 시그널로 사용된다.

## 계산
- 최근 14 bar 의 종가·거래량에서 RSI 를 계산하고, 가격 고점/저점과 RSI 고점/저점의 방향이 반대일 때 divergence 플래그.
