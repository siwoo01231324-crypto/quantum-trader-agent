---
type: strategy
id: live-macd-bullish-cross-breakout
name: Live MACD Bullish Cross + 20-bar Breakout
status: backtest
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
  장중 실시간 검색식. MACD 히스토그램이 음수에서 양수로 골든크로스
  하면서 동시에 직전 20봉 신고가를 돌파하면 매수. 두 조건 동시 충족
  요구로 false-positive 줄임. 청산은 손절 -3% / 익절 +6%.
tags:
- live-scanner
- macd
- breakout
- intraday
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
