---
type: strategy
id: momo-kis-v1
name: KIS KRX 15m Momentum v1
status: backtest
instruments: [krx-005930]
timeframe: 15m
uses_signals: [rsi-divergence]
risk_rules: [max-drawdown-5pct]
owner: siwoo
created: 2026-04-25
sharpe_bt: null
sharpe_live: null
mdd_bt: null
annual_return_bt: null
backtest_period: null
last_updated: 2026-05-05
summary_ko: |
  삼성전자(005930) 15분봉 인트라데이 전략.
  RSI 강세 다이버전스(가격은 떨어졌는데 RSI는 덜 떨어진 상태) 시 매수, 약세 다이버전스 시 전량 청산.
  KRX 마감 15:30 직전 자동 평탄화. 데이트레이딩 호흡.
tags: [momentum, krx, intraday]
---

# KIS KRX 15m Momentum v1

KRX 상장 005930 (삼성전자) 15분봉 기준 모멘텀 전략. 진입 신호로 [[rsi-divergence]] 를 사용하고, 리스크 통제는 [[max-drawdown-5pct]] 규칙을 적용한다.

## 진입

- [[rsi-divergence]] 가 bullish divergence 일 때 롱 진입.
- RSI period=14, divergence lookback=14, warmup=43 bars (RSI_PERIOD + LOOKBACK*2 + 1).

## 진입 크기

- `sizing_mode="half-kelly"` (기본) — 최근 60 bar μ/σ 로 `kelly_continuous` → Half Kelly 적용.
- EWMA σ (λ=0.94) 로 변동성 추정. σ ≤ 1e-9 → size=0 fallback (VI 단일가 구간 보호).
- 사이징 수학은 [[20-position-sizing]] 에 정의된 순수 함수 경유; LLM 개입 금지.

## 청산

- bearish RSI divergence 발생 시 전량 청산 (size=1.0).
- KRX 마감(15:30 KST) 이후 bars 는 `_is_my_bar_boundary` 에서 reject → 자동 강제 평탄.

## 바 바운더리

- KST 09:00~15:30, 15분 단위 (`minute % 15 == 0 and second == 0`).
- weekday < 5 (월~금) AND `is_krx_holiday` 게이트 — 2중 안전망.
- harness(orchestrator) + 전략 자체 `_is_my_bar_boundary` 양쪽 체크: 어느 한 쪽이 무너져도 leakage 차단.

## 리스크 연동

```python
from portfolio import AsyncStrategyOrchestrator
orch.register_strategy("momo_kis_v1", strategy)
orch.register_strategy_returns("momo_kis_v1", daily_return_series)
orch.refresh_portfolio_risk()
```

- `daily_return_series: pd.Series` — index=날짜, 값=그날 실현 수익률.
- 미등록 시 `portfolio_risk is None` → 리스크 평가기가 항상 ALLOW → 리스크 관리 무력화.
- 상세: [[19-portfolio-risk]] §2.2, §3.1, §4.1.

## 관련 노트

- [[rsi-divergence]] — 진입 신호 정의
- [[max-drawdown-5pct]] — 리스크 규칙
- [[19-portfolio-risk]] — 멀티 전략 포트폴리오 리스크 모델
- [[20-position-sizing]] — half-kelly 사이징 이론
