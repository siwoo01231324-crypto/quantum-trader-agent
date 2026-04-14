---
type: strategy
id: momo-btc-v2
name: BTC Momentum v2
status: backtest
instruments: [BTCUSDT]
timeframe: 15m
uses_signals: [rsi-divergence]
risk_rules: [max-drawdown-5pct]
owner: siwoo
created: 2026-04-14
sharpe_bt: 1.82
sharpe_live: null
tags: [momentum, crypto]
---

# BTC Momentum v2

BTC 무기한 선물 15분봉 기준 모멘텀 전략. 진입 신호로 [[rsi-divergence]] 를 사용하고, 리스크 통제는 [[max-drawdown-5pct]] 규칙을 적용한다. 거래 대상은 [[BTCUSDT]] 한 종목.

## 진입
- [[rsi-divergence]] 가 bullish divergence 일 때 롱, bearish 일 때 숏.

## 청산
- 반대 divergence 발생, 또는 [[max-drawdown-5pct]] halt 시.
