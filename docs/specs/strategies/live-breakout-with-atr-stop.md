---
type: strategy
id: live-breakout-with-atr-stop
name: Live 20-bar Breakout (Trailing-Stop Exit)
status: backtest
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
sharpe_bt: null
sharpe_live: null
mdd_bt: null
annual_return_bt: null
trades_bt: null
backtest_period: null
last_updated: 2026-05-11
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
