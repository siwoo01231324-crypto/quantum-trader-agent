---
type: strategy
id: cand-c-2026-05-20-live-rsi-oversold-volume-spike
name: "[Cand-C 2026-05-20] Live RSI Oversold + Volume Spike (BN 1d)"
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
sharpe_bt: 1.140
sharpe_live: null
mdd_bt: -0.4328
annual_return_bt: null
trades_bt: 285
backtest_period: 2021-05-20/2026-05-20
profit_factor_bt: 2.049
expectancy_bt: 0.0316
verdict_5y: "PASS: PF=2.049>1, expectancy=+3.16%/trade>0, DSR=0.968 (5y BN USDT-perp 28 syms, cost 10bp). Candidate-C STRONG member (weight 0.30)."
summary_ko: |
  Candidate-C 2026-05-20 deployment 의 STRONG member (weight 0.30).
  부모 코드/시그널: [[live-rsi-oversold-volume-spike]] (status: rejected on 1m,
  same code re-deployed at 1d bar). 5y BN 1d 결과 PF=2.05/DSR=0.968 PASS,
  walk-forward 6/7 (86%) 일관성. default_size=0.0075
  (= baseline 0.05 × weight 0.30 × half_kelly 0.5).
tags:
- live-scanner
- bn1d
- rsi
- candidate-c
- 2026-05-20
- experimental
---

# [Cand-C 2026-05-20] Live RSI Oversold + Volume Spike (BN 1d)

본 spec 은 **Candidate-C 2026-05-20 deployment** 의 member 1/4. 부모 sub-strategy `live-rsi-oversold-volume-spike` 의 코드를 그대로 재사용하되, **timeframe=1d** 환경 + `default_size=0.0075` 로 운영되는 별도 deployment instance.

## 부모 spec 참조

- 코드·시그널·진입 룰: [[live-rsi-oversold-volume-spike]]
- 백그라운드 검증: [[51-live-scanner-bn1d-ensemble-validation]]

## 본 deployment 의 차이

| 항목 | 부모 (live-rsi-oversold-volume-spike) | 본 deployment (cand-c) |
|---|---|---|
| status | rejected (1m 결과) | **experimental-bn1d** |
| timeframe | 1m (live-scanner intraday) | **1d** (universe 일봉 평가) |
| default_size | 0.05 (가정) | **0.0075** (= 0.05 × 0.30 × 0.5) |
| 진입 조건 | 동일 (RSI<30 + volume spike) | 동일 |
| stop/tp | 3%/6% | 동일 |

## 5y BN 1d 검증 (background/51 §3.2)

- trades: 285
- win_rate: 62.46%
- payoff: 1.23
- **PF: 2.049** ✅
- **expectancy: +3.16%/trade** ✅
- SR (진짜 daily series): 1.140
- **DSR: 0.968 ✅ PASS** (4 trials 보정)
- MDD: -43.28%
- walk-forward: **6/7 연도 (86%) 일관성** — STRONG

## 운영 규칙

- LLM 호출 금지 (불변식 #6).
- 활성화 게이트 (production.yaml + 2 ENV vars):
  - production.yaml entry 활성화 (uncomment 완료)
  - ENV `LIVE_SCANNER_ENABLED=1` (LivePositionRiskManager 와이어)
  - ENV `LIVE_SCANNER_BN1D_ENSEMBLE_ENABLED=1` (Candidate-C 그룹 게이트)
- paper 6개월 운영 후 자본 확대 검토.
- 자동 trip (3개월 rolling PF<1 → ENV gate OFF) 코드 미구현 — 수동 모니터링.

## 리스크 연동

```python
orch.register_strategy(
    "cand-c-2026-05-20-live-rsi-oversold-volume-spike",
    LiveRsiOversoldVolumeSpike(default_size=0.0075),
)
orch.register_strategy_returns(
    "cand-c-2026-05-20-live-rsi-oversold-volume-spike", daily_returns_series,
)
```

## Caveats

(background/51 §5 와 동일 — 5y single-window, 비용 모델 단순, 다중검정 한 단계만 PBO 보정)

## 관련

- [[live-rsi-oversold-volume-spike]] — 부모 sub-strategy spec (1m rejected)
- [[51-live-scanner-bn1d-ensemble-validation]] — 출처 검증 노트
- [[cand-c-2026-05-20-live-breakout-with-atr-stop]] — Cand-C member 2/4 (STRONG)
- [[cand-c-2026-05-20-live-bb-lower-bounce]] — Cand-C member 3/4 (WEAK 분산자산)
- [[cand-c-2026-05-20-live-oversold-with-divergence]] — Cand-C member 4/4 (WEAK)
