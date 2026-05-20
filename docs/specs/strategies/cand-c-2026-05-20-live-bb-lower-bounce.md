---
type: strategy
id: cand-c-2026-05-20-live-bb-lower-bounce
name: "[Cand-C 2026-05-20] Live BB Lower Band Bounce (BN 1d)"
status: experimental-bn1d
paradigm: live-scanner
instruments:
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 1d
uses_signals:
- bollinger
risk_rules:
- per-symbol-stop-loss-3pct
- per-symbol-take-profit-6pct
owner: siwoo
created: 2026-05-20
last_updated: 2026-05-20
stop_loss_pct: 0.03
take_profit_pct: 0.06
trailing_stop_pct: null
sharpe_bt: 0.216
sharpe_live: null
mdd_bt: -0.7129
annual_return_bt: null
trades_bt: 668
backtest_period: 2021-05-20/2026-05-20
profit_factor_bt: 0.646
expectancy_bt: -0.0172
verdict_5y: "WEAK (diversifier): solo PF=0.646<1 / DSR=0.000 FAIL, but ensemble member (weight 0.20) where it provides decorrelation with STRONG members (4-parallel MDD -23% vs solo STRONG -89%)."
summary_ko: |
  Candidate-C 2026-05-20 deployment 의 WEAK member (weight 0.20).
  부모 코드/시그널: [[live-bb-lower-bounce]] (status: rejected on 1m).
  **솔로로는 BN 1d 도 LOSE (PF=0.65/DSR=0.00 FAIL)**. 그러나 STRONG 2종과의
  상관성이 낮아 4-parallel ensemble 의 분산자산으로 기여 (background/51
  §3.4 — MDD 절반 효과). default_size=0.0050 (= 0.05 × 0.20 × 0.5).
tags:
- live-scanner
- bn1d
- bollinger
- mean-reversion
- candidate-c
- 2026-05-20
- weak-diversifier
- experimental
---

# [Cand-C 2026-05-20] Live BB Lower Band Bounce (BN 1d)

본 spec 은 **Candidate-C 2026-05-20 deployment** 의 member 3/4 — **WEAK 분산자산**. 부모 sub-strategy 의 코드를 그대로 재사용하되 ensemble 안에서 운영.

## 부모 spec 참조

- 코드·시그널·진입 룰: [[live-bb-lower-bounce]]
- 백그라운드 검증: [[51-live-scanner-bn1d-ensemble-validation]]

## 5y BN 1d 검증 (background/51 §3.2) — 솔로 LOSE

- trades: 668
- win_rate: 37.87%
- payoff: 1.06
- **PF: 0.646 ❌ < 1**
- **expectancy: -1.72%/trade ❌**
- SR: 0.216
- DSR: 0.000 ❌ FAIL
- MDD: -71.3%
- walk-forward: 5/7 (71%) 보통

## 그럼에도 ensemble 에 포함하는 이유

WEAK 솔로 멤버지만, 4-parallel ensemble 에서:
- STRONG 2종(rsi/breakout)과의 **상관성 낮음** → 분산자산 역할
- 4-parallel 합산 MDD: -23.2% (STRONG 2종만 운영 시 MDD -66% 대비)
- background/51 §3.4 의 portfolio sim 이 정량 확인 — WEAK 가 추가될수록 ensemble MDD 가 큰 폭으로 감소

**모니터링**: 솔로 음의 엣지 + 분산 효과 trade-off. ensemble 결과가 backtest 와 deviate 하면 weight 0.20 → 0 (drop) 검토.

## 운영 규칙

(다른 Cand-C member spec 과 동일)

## 관련

- [[live-bb-lower-bounce]] — 부모 sub-strategy spec
- [[51-live-scanner-bn1d-ensemble-validation]] — 출처 검증 노트
- 자매 Cand-C members: [[cand-c-2026-05-20-live-rsi-oversold-volume-spike]] · [[cand-c-2026-05-20-live-breakout-with-atr-stop]] · [[cand-c-2026-05-20-live-oversold-with-divergence]]
