---
type: strategy
id: momo-btc-v2
name: BTC Momentum v2
status: paper
instruments: [BTCUSDT]
timeframe: 15m
uses_signals: [rsi-divergence]
risk_rules: [max-drawdown-5pct]
owner: siwoo
created: 2026-04-14
tags: [momentum, crypto]
---

BTC 15분봉 모멘텀 전략. [[rsi-divergence]] 시그널을 사용하고 [[max-drawdown-5pct]] 규칙을 적용한다.
