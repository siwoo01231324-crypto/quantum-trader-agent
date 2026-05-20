---
type: strategy
id: cand-c-2026-05-20-live-oversold-with-divergence
name: "[Cand-C 2026-05-20] Live Oversold with Divergence (BN 1d)"
status: experimental-bn1d
paradigm: live-scanner
instruments:
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 1d
uses_signals:
- rsi
risk_rules:
- per-symbol-stop-loss-3pct
- per-symbol-take-profit-6pct
owner: siwoo
created: 2026-05-20
last_updated: 2026-05-20
stop_loss_pct: 0.005
take_profit_pct: 0.010
trailing_stop_pct: null
sharpe_bt: 0.773
sharpe_live: null
mdd_bt: -0.7533
annual_return_bt: null
trades_bt: 1167
backtest_period: 2021-05-20/2026-05-20
profit_factor_bt: 0.729
expectancy_bt: -0.0124
verdict_5y: "WEAK (diversifier): solo PF=0.729<1 / DSR=0.000 FAIL, but ensemble member (weight 0.20) — same diversifier role as bb_lower_bounce."
summary_ko: |
  Candidate-C 2026-05-20 deployment 의 WEAK member (weight 0.20).
  부모 코드/시그널: [[live-oversold-with-divergence]] (status: rejected on 1m).
  솔로 BN 1d 도 LOSE (PF=0.73/DSR=0.00 FAIL). bb_lower_bounce 와 동일하게
  4-parallel ensemble 의 분산자산. default_size=0.0050 (= 0.05 × 0.20 × 0.5).
tags:
- live-scanner
- bn1d
- divergence
- candidate-c
- 2026-05-20
- weak-diversifier
- experimental
---

# [Cand-C 2026-05-20] Live Oversold with Divergence (BN 1d)

본 spec 은 **Candidate-C 2026-05-20 deployment** 의 member 4/4 — **WEAK 분산자산**.

## 부모 spec 참조

- 코드·시그널·진입 룰: [[live-oversold-with-divergence]]
- 백그라운드 검증: [[51-live-scanner-bn1d-ensemble-validation]]

## 5y BN 1d 검증 (background/51 §3.2) — 솔로 LOSE

- trades: 1,167
- win_rate: 38.30%
- payoff: 1.17
- **PF: 0.729 ❌ < 1**
- **expectancy: -1.24%/trade ❌**
- SR: 0.773
- DSR: 0.000 ❌ FAIL
- MDD: -75.33%
- walk-forward: 5/7 (71%) 보통

## ensemble 분산자산 역할

(bb_lower_bounce 와 동일 — STRONG 2종과 상관성 낮아 4-parallel MDD 감소에 기여. background/51 §3.4 portfolio sim 참조.)

## 운영 규칙

(다른 Cand-C member spec 과 동일)

## 관련

- [[live-oversold-with-divergence]] — 부모 sub-strategy spec
- [[51-live-scanner-bn1d-ensemble-validation]] — 출처 검증 노트
- 자매 Cand-C members: [[cand-c-2026-05-20-live-rsi-oversold-volume-spike]] · [[cand-c-2026-05-20-live-breakout-with-atr-stop]] · [[cand-c-2026-05-20-live-bb-lower-bounce]]
