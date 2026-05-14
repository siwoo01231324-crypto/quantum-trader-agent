---
type: strategy
id: live-bb-lower-bounce
name: Live BB Lower Band Bounce
status: backtest
paradigm: live-scanner
instruments:
- KRX_UNIVERSE
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 1m
uses_signals:
- bollinger
risk_rules:
- per-symbol-stop-loss-3pct
- per-symbol-take-profit-6pct
owner: siwoo
created: 2026-05-11
sharpe_bt: null
sharpe_live: null
mdd_bt: null
annual_return_bt: null
trades_bt: null
backtest_period: null
last_updated: 2026-05-11
stop_loss_pct: 0.03
take_profit_pct: 0.06
trailing_stop_pct: null
summary_ko: |
  장중 실시간 검색식. 볼린저 하단을 직전 봉에서 이탈했다가 당일 봉에서
  다시 회복하고 동시에 거래량이 평균 이상이면 매수 (mean-reversion).
  청산은 손절 -3% / 익절 +6%.
tags:
- live-scanner
- bollinger
- mean-reversion
- intraday
---

# Live BB Lower Band Bounce

장중 실시간 검색식 (#227 S4). 볼린저 하단 이탈 후 회복 + 거래량 확인 시 평균회귀 진입.

## 진입

- `close[-2] < bb_lower[-2]` — 직전 봉이 하단 이탈
- AND `close[-1] > bb_lower[-1]` — 당일 봉이 하단 회복
- AND `volume[-1] >= mean(volume[-21:-1])` — 매수세 확인 (false bounce 차단)

## 청산

본 전략은 sell signal 을 발행하지 않는다. 청산은 `LivePositionRiskManager` 책임:
- `stop_loss_pct = 0.03`, `take_profit_pct = 0.06`, `trailing_stop_pct = null`

## 리스크 연동

```python
orch.register_strategy("live_bb_lower_bounce", LiveBbLowerBounce())
orch.register_strategy_returns("live_bb_lower_bounce", daily_returns_series)
```

## 백테스트

- 5y bench 미실시 — #227 S6 단계에서 검증 예정

## 운영 규칙

- LLM 호출 금지 (불변식 #6)
- 활성화 게이트: `LIVE_SCANNER_ENABLED=1` + `production.yaml` `enabled: true`
- 단위 테스트: `tests/backtest/test_live_bb_lower_bounce.py`

## 관련

- `docs/specs/live-universe-scanner-paradigm.draft.md` — 본 패러다임 spec
- 이슈 #227 (Live Universe Scanner — 진행 중)
