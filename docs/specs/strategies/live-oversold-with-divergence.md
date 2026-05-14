---
type: strategy
id: live-oversold-with-divergence
name: Live RSI Bullish Divergence in Downtrend
status: backtest
paradigm: live-scanner
instruments:
- KRX_UNIVERSE
- BINANCE_USDT_PERP_UNIVERSE
timeframe: 1m
uses_signals:
- rsi
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
  장중 실시간 검색식. 가격은 21봉 전 대비 하락 (downtrend) + RSI 다이버전스
  (가격은 신저점인데 RSI 는 더 안 떨어짐) 발생 시 매수. momo_kis_v1 의
  단일종목 룰을 universe-wide 로 확장한 변형. 청산은 손절 -3% / 익절 +6%.
tags:
- live-scanner
- rsi
- divergence
- intraday
- mean-reversion
---

# Live RSI Bullish Divergence in Downtrend

장중 실시간 검색식 (#227 S4). 하락 추세 안에서 RSI 다이버전스 발견 시 변곡점 진입. `momo_kis_v1` 의 single-ticker (005930) 룰을 universe 380종으로 확장.

## 진입

- `close[-1] < close[-22]` — 21봉 downtrend 필터 (sideways 차단)
- AND `detect_divergence(close, rsi, 14)[-1] == 'bullish'` — 가격 신저점인데 RSI 더 안 떨어짐

## 청산

본 전략은 sell signal 을 발행하지 않는다. 청산은 `LivePositionRiskManager` 책임:
- `stop_loss_pct = 0.03`, `take_profit_pct = 0.06`, `trailing_stop_pct = null`

## momo_kis_v1 와의 차이

| 구분 | `momo_kis_v1` (legacy) | 본 전략 (universe-wide) |
|---|---|---|
| 종목 | 005930 단일 | KRX 350 + Binance 30 |
| Bar boundary | KRX 15분봉 | 매 tick (1분봉 cache) |
| 청산 | bearish divergence 시 sell signal 자체 발행 | strategy 가 sell 안 함 — `LivePositionRiskManager` 자동 |
| 사이징 | half-Kelly per signal | 고정 5% (포트폴리오 limit 가 게이팅) |
| Downtrend 필터 | 없음 | `close[-1] < close[-22]` 추가 |

## 리스크 연동

```python
orch.register_strategy("live_oversold_with_divergence", LiveOversoldWithDivergence())
orch.register_strategy_returns("live_oversold_with_divergence", daily_returns_series)
```

## 백테스트

- 5y bench 미실시 — #227 S6 단계에서 검증 예정. KRX universe 결과를 `momo_kis_v1` 005930 단일종목 5y bench (있으면) 대비 알파 비교 권장

## 운영 규칙

- LLM 호출 금지 (불변식 #6) — RSI / divergence 모두 결정적 코드
- 활성화 게이트: `LIVE_SCANNER_ENABLED=1` + `production.yaml` `enabled: true`
- 단위 테스트: `tests/backtest/test_live_oversold_with_divergence.py`

## 관련

- `docs/specs/live-universe-scanner-paradigm.draft.md` — 본 패러다임 spec
- `docs/specs/strategies/momo-kis-v1.md` (legacy single-ticker — universe 화 대상)
- 이슈 #227 (Live Universe Scanner — 진행 중)
