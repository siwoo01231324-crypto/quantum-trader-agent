---
type: strategy
id: live-macd-bullish-cross-breakout
name: Live MACD Bullish Cross + 20-bar Breakout
status: rejected
paradigm: live-scanner
instruments:
- KRX_UNIVERSE
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 1m
uses_signals:
- macd
risk_rules:
- per-symbol-stop-loss-3pct
- per-symbol-take-profit-6pct
owner: siwoo
created: 2026-05-11
sharpe_bt: 1.499
sharpe_live: null
mdd_bt: -0.5003
annual_return_bt: 0.7312
trades_bt: 39982
backtest_period: 2021-05-19/2026-05-19
last_updated: 2026-05-20
stop_loss_pct: 0.03
take_profit_pct: 0.06
trailing_stop_pct: null
summary_ko: |
  장중 실시간 검색식. MACD 히스토그램이 음수에서 양수로 골든크로스
  하면서 동시에 직전 20봉 신고가를 돌파하면 매수. 두 조건 동시 충족
  요구로 false-positive 줄임. 청산은 손절 -3% / 익절 +6%.
tags:
- live-scanner
- macd
- breakout
- intraday
profit_factor_bt: 0.8846
expectancy_bt: -0.002637
verdict_5y: "rejected: PF=0.885<1, expectancy=-0.264%/trade<0 (5y/30 syms/10bp)"
---

# Live MACD Bullish Cross + 20-bar Breakout

장중 실시간 검색식 (#227 S4). MACD 히스토그램 골든크로스 와 20봉 신고가 돌파 동시 충족 시 진입.

## 진입

- `histogram[-2] <= 0` AND `histogram[-1] > 0` — MACD bullish cross
- AND `close[-1] >= max(close[-21:-1])` — 20봉 신고가 돌파
- 두 조건 모두 같은 봉에서 충족돼야 함 (false-positive 감소)

## 청산

본 전략은 sell signal 을 발행하지 않는다. 청산은 `LivePositionRiskManager` 책임:
- `stop_loss_pct = 0.03`, `take_profit_pct = 0.06`, `trailing_stop_pct = null`

## 리스크 연동

```python
orch.register_strategy("live_macd_bullish_cross_breakout", LiveMacdBullishCrossBreakout())
orch.register_strategy_returns("live_macd_bullish_cross_breakout", daily_returns_series)
```

## 백테스트

- 5y bench 미실시 — #227 S6 단계에서 KRX + Binance universe 양쪽 검증 예정
- Sharpe ≥ 0.5 통과 시 production.yaml `enabled: true` 후보

## 운영 규칙

- LLM 호출 금지 (불변식 #6) — MACD 모두 결정적 코드
- 활성화 게이트: `LIVE_SCANNER_ENABLED=1` + `production.yaml` `enabled: true`
- 단위 테스트: `tests/backtest/test_live_macd_bullish_cross_breakout.py`

## 관련

- `docs/specs/live-universe-scanner-paradigm.draft.md` — 본 패러다임 spec
- 이슈 #227 (Live Universe Scanner — 진행 중)

## 5y 검증 결과 (2026-05-20)

**REJECTED.** 견고지표(Profit Factor·거래당 기대값) 기준 음의 엣지 확정.

| 지표 | 값 | 게이트 |
|---|---|---|
| Profit Factor | **0.885** | <1 ❌ |
| 기대값/거래 | **-0.264%** | <0 ❌ |
| 승률 | 33.2% | — |
| Payoff | 1.78x | — |
| 거래수 | 39,982 | — |

조건: 5y(2021-05~2026-05) · 30 USDT-perp 심볼 · 라운드트립 비용 10bp.

벤치 하네스의 Sharpe (1.50) 는 `bench_live_scanner._aggregate` 의 일별평균 + `final ** (252/n_days_with_trades)` 투영 집계 산물로, PF<1 과 부호가 모순되어 **신뢰 불가**. 결정 근거는 PF·기대값 (게임 불가능, 합산 기반).

사전등록 가설(naive 진입 + 고정 % 출구) **falsified**. 파라미터 튜닝(stop/TP/trailing %)으로 PF 1 못 넘김 — `scripts/sweep_breakout_atr.py` 의 1y(19조합) + 5y(3조합) sweep 이 이미 증명. 재활성화는 (a) 진입 신호 재설계 + (b) PF>1·exp>0 게이트 통과 후에만.

원자료: `reports/eval_live_scanners_5y.json`.
