---
type: strategy
id: meanrev-pairs
name: ETHBTC 1h Mean Reversion Pairs
status: backtest
instruments:
- ETHBTC
timeframe: 1h
uses_signals:
- zscore
- realized-vol
risk_rules:
- max-drawdown-5pct
owner: siwoo
created: 2026-04-25
sharpe_bt: null
sharpe_live: null
tags:
- mean-reversion
- crypto
- pairs
---

# ETHBTC 1h Mean Reversion Pairs

ETHBTC 1시간봉 기준 로그가격 z-score 를 이용한 평균회귀 전략. ETH/BTC 비율이 60봉 이동평균 대비 -2σ 이하로 하락 시 진입 (회복 기대), z > 0 에서 청산.

## 진입

- `z = (log(close) - rolling_mean(log(close), 60)) / rolling_std(log(close), 60)`
- z < -2.0 이면 **buy** (비율이 평균 이하로 하락 — 회복 기대)
- |z| <= 2.0 이면 **hold**

## 진입 크기

- EWMA σ (λ=0.94) 기반 `vol_target(sigma, target_annual=0.15, periods_per_year=365*24)`.
- 연 15% 변동성 목표 (1h bar 기준).

## 청산

- z > 0 이면 **sell** (비율이 평균 위로 회복 — 청산).
- 전량 청산 (size=1.0).

## 훅 소비

- `required_factors: ClassVar[list[str]] = ["zscore", "realized_vol"]`
- `signals.compute("zscore", close=close, window=60)` — log-price 도메인 z-score.
- `signals.compute("realized_vol", close=close, window=60, annualize=365*24)` — 1h 연환산 변동성.
- Bar boundary: `ts.minute == 0 and ts.second == 0` (매 정시 UTC).

## 리스크 연동

```python
orchestrator.register_strategy("meanrev_pairs", strategy)
orchestrator.register_strategy_returns("meanrev_pairs", daily_return_series)
```

- `daily_return_series`: index=date, 값=비용 차감 일수익률 (instrument_type="crypto", cost_rate=0.001 편도).
- `apply_cost(returns, positions, "crypto")` 적용 후 공급.

## 관련 노트

- [[19-portfolio-risk]] — ENB/ρ 게이트 (enb_ratio >= 0.5, avg ρ <= 0.6)
- [[20-position-sizing]] — vol_target 이론적 근거
- [[13-feature-alpha-catalog]] — 팩터 카탈로그 (zscore, realized_vol 스펙)
- [[01_plan]] — 전략 카탈로그 확장 구현 계획
