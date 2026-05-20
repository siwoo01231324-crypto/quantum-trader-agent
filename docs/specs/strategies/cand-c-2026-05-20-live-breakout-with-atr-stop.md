---
type: strategy
id: cand-c-2026-05-20-live-breakout-with-atr-stop
name: "[Cand-C 2026-05-20] Live Breakout with ATR Stop (BN 1d)"
status: experimental-bn1d
paradigm: live-scanner
instruments:
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 1d
uses_signals:
- atr
- donchian
risk_rules:
- per-symbol-stop-loss-5pct
- per-symbol-take-profit-20pct
- per-symbol-trailing-stop-4pct
owner: siwoo
created: 2026-05-20
last_updated: 2026-05-20
stop_loss_pct: 0.005
take_profit_pct: 0.010
trailing_stop_pct: 0.005
sharpe_bt: 2.443
sharpe_live: null
mdd_bt: -0.8892
annual_return_bt: null
trades_bt: 1803
backtest_period: 2021-05-20/2026-05-20
profit_factor_bt: 1.326
expectancy_bt: 0.0143
verdict_5y: "PASS: PF=1.326>1, expectancy=+1.43%/trade>0, DSR=1.000 (5y BN USDT-perp 28 syms, cost 10bp). Candidate-C STRONG member (weight 0.30)."
summary_ko: |
  Candidate-C 2026-05-20 deployment 의 STRONG member (weight 0.30).
  부모 코드/시그널: [[live-breakout-with-atr-stop]] (status: rejected on 1m,
  same code re-deployed at 1d bar). 5y BN 1d 결과 PF=1.33/DSR=1.000 PASS,
  walk-forward 6/7 (86%) 일관성. default_size=0.0075
  (= baseline 0.05 × weight 0.30 × half_kelly 0.5).
tags:
- live-scanner
- bn1d
- breakout
- atr
- candidate-c
- 2026-05-20
- experimental
---

# [Cand-C 2026-05-20] Live Breakout with ATR Stop (BN 1d)

본 spec 은 **Candidate-C 2026-05-20 deployment** 의 member 2/4. 부모 sub-strategy `live-breakout-with-atr-stop` 의 코드를 그대로 재사용하되, **timeframe=1d** + `default_size=0.0075` 로 운영되는 별도 deployment instance.

## 부모 spec 참조

- 코드·시그널·진입 룰: [[live-breakout-with-atr-stop]]
- 백그라운드 검증: [[51-live-scanner-bn1d-ensemble-validation]]

## 5y BN 1d 검증 (background/51 §3.2)

- trades: 1,803
- win_rate: 38.21%
- payoff: 2.14
- **PF: 1.326** ✅
- **expectancy: +1.43%/trade** ✅
- SR (진짜 daily series): 2.443
- **DSR: 1.000 ✅ PASS**
- MDD: -88.92%
- walk-forward: **6/7 연도 (86%) 일관성** — STRONG

## 운영 규칙

(다른 Cand-C member spec 과 동일)

## 관련

- [[live-breakout-with-atr-stop]] — 부모 sub-strategy spec
- [[51-live-scanner-bn1d-ensemble-validation]] — 출처 검증 노트
- 자매 Cand-C members: [[cand-c-2026-05-20-live-rsi-oversold-volume-spike]] · [[cand-c-2026-05-20-live-bb-lower-bounce]] · [[cand-c-2026-05-20-live-oversold-with-divergence]]
