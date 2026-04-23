---
type: strategy
id: momo-btc-v2
name: BTC Momentum v2
status: backtest
instruments:
- BTCUSDT
timeframe: 15m
uses_signals:
- rsi-divergence
risk_rules:
- max-drawdown-5pct
owner: siwoo
created: 2026-04-14
sharpe_bt: 0.1847
sharpe_live: null
tags:
- momentum
- crypto
---

# BTC Momentum v2

BTC 무기한 선물 15분봉 기준 모멘텀 전략. 진입 신호로 [[rsi-divergence]] 를 사용하고, 리스크 통제는 [[max-drawdown-5pct]] 규칙을 적용한다. 거래 대상은 [[BTCUSDT]] 한 종목.

## 진입
- [[rsi-divergence]] 가 bullish divergence 일 때 롱, bearish 일 때 숏.

## 진입 크기
- 기본 `sizing_mode="full"` (all-in, size=1.0) — 기존 거동 유지.
- 옵션: `sizing_mode="half-kelly"` — 최근 60 bar μ/σ 로 `kelly_continuous` → Half Kelly 적용.
- 옵션: `sizing_mode="vol-target"` — EWMA σ(λ=0.94) 로 연 20% 목표 사이징.
- 사이징 수학은 [[position-sizing]] 에 정의된 순수 함수 경유; LLM 개입 금지.

## 청산
- 반대 divergence 발생, 또는 [[max-drawdown-5pct]] halt 시. 매도 시 항상 전량(size=1.0).

## 관련 노트

- [[13-feature-alpha-catalog]] — RSI 계산 로직·룩어헤드 방지 규칙
- [[12-validation-protocol]] — 본 전략의 백테스트 검증 (walk-forward, DSR/PBO)
- [[20-position-sizing]] — 진입 크기 이론적 근거 (Half Kelly + vol targeting)
- [[position-sizing]] — 사이저 구현 스펙 (`sizing_mode` 옵션)
- [[19-portfolio-risk]] — 멀티 전략 운영 시 상관·공분산 관리
- [[execution-algorithms]] — 주문 실행 (Market/Limit/TWAP)
- [[kill-switch-runbook]] — MDD halt 발생 시 청산 절차
