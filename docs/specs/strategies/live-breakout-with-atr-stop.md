---
type: strategy
id: live-breakout-with-atr-stop
name: Live 20-bar Breakout (Trailing-Stop Exit)
status: rejected
paradigm: live-scanner
instruments:
- KRX_UNIVERSE
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 1m
uses_signals:
- atr
risk_rules:
- per-symbol-trailing-stop-4pct
owner: siwoo
created: 2026-05-11
sharpe_bt: 3.193
sharpe_live: null
mdd_bt: -0.355
annual_return_bt: 2.6085
trades_bt: 50088
backtest_period: 2021-05-19/2026-05-19
last_updated: 2026-05-20
stop_loss_pct: 0.05
take_profit_pct: 0.20
trailing_stop_pct: 0.04
summary_ko: |
  장중 실시간 검색식. 직전 20봉 신고가 돌파 시 매수. 청산은 trailing-stop
  4% 가 주된 룰 — 가격이 신고가 갱신할 때마다 따라 올라가다가 4% 후퇴
  하면 매도. 손절 -5% / 익절 +20% 는 극단 outlier 만 잡는 안전망.
tags:
- live-scanner
- breakout
- trailing-stop
- intraday
profit_factor_bt: 0.8683
expectancy_bt: -0.002429
verdict_5y: "rejected: PF=0.868<1, expectancy=-0.243%/trade<0 (5y/30 syms/10bp)"
---

# Live 20-bar Breakout (Trailing-Stop Exit)

장중 실시간 검색식 (#227 S4). 단순 20봉 신고가 돌파 진입 + trailing-stop 위주 청산. 추세 추종형.

## 진입

- `close[-1] >= max(close[-21:-1])` — 20봉 신고가 돌파

다른 검색식과 다르게 RSI / 거래량 조건을 추가하지 않음. 이유: trailing-stop 이 false-positive 의 손실을 4% 로 묶음 → 진입 hurdle 을 낮춰 trade 수를 늘릴 가치가 있음 (5y backtest 가 가설 검증).

## 청산

본 전략은 sell signal 을 발행하지 않는다. 청산은 `LivePositionRiskManager`:
- `stop_loss_pct = 0.05` — 매수가 -5% 안전망
- `take_profit_pct = 0.20` — 매수가 +20% 안전망 (드물게 발동)
- **`trailing_stop_pct = 0.04`** — 주된 청산 룰. 매수 후 갱신된 최고가 대비 -4% 후퇴 시 매도

## 리스크 연동

```python
orch.register_strategy("live_breakout_with_atr_stop", LiveBreakoutWithAtrStop())
orch.register_strategy_returns("live_breakout_with_atr_stop", daily_returns_series)
```

## 백테스트

- 5y bench 미실시 — #227 S6 단계에서 검증 예정. 추세 추종 전략의 trailing-stop 규모 sensitivity (2% / 4% / 6%) sweep 권장

## 운영 규칙

- LLM 호출 금지 (불변식 #6)
- 활성화 게이트: `LIVE_SCANNER_ENABLED=1` + `production.yaml` `enabled: true`
- 단위 테스트: `tests/backtest/test_live_breakout_with_atr_stop.py`

## 관련

- `docs/specs/live-universe-scanner-paradigm.draft.md` — 본 패러다임 spec
- 이슈 #227 (Live Universe Scanner — 진행 중)

## 5y 검증 결과 (2026-05-20)

**REJECTED.** 견고지표(Profit Factor·거래당 기대값) 기준 음의 엣지 확정.

| 지표 | 값 | 게이트 |
|---|---|---|
| Profit Factor | **0.868** | <1 ❌ |
| 기대값/거래 | **-0.243%** | <0 ❌ |
| 승률 | 34.3% | — |
| Payoff | 1.67x | — |
| 거래수 | 50,088 | — |

조건: 5y(2021-05~2026-05) · 30 USDT-perp 심볼 · 라운드트립 비용 10bp.

벤치 하네스의 Sharpe (3.19) 는 `bench_live_scanner._aggregate` 의 일별평균 + `final ** (252/n_days_with_trades)` 투영 집계 산물로, PF<1 과 부호가 모순되어 **신뢰 불가**. 결정 근거는 PF·기대값 (게임 불가능, 합산 기반).

사전등록 가설(naive 진입 + 고정 % 출구) **falsified**. 파라미터 튜닝(stop/TP/trailing %)으로 PF 1 못 넘김 — `scripts/sweep_breakout_atr.py` 의 1y(19조합) + 5y(3조합) sweep 이 이미 증명. 재활성화는 (a) 진입 신호 재설계 + (b) PF>1·exp>0 게이트 통과 후에만.

원자료: `reports/eval_live_scanners_5y.json`.
