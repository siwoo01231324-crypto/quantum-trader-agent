---
type: strategy
id: live-rsi-oversold-volume-spike
name: Live RSI Oversold + Volume Spike
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
  장중 실시간 검색식. RSI(14) 가 30 미만으로 떨어지고 동시에 마지막 봉의 거래량이
  최근 20봉 평균의 2배를 초과하면 진입. 손익비 청산 (-3% / +6%) 은 별도
  LivePositionRiskManager 가 담당하며, 본 전략은 매수 신호만 발행한다.
tags:
- live-scanner
- rsi
- volume
- intraday
---

# Live RSI Oversold + Volume Spike

장중 실시간 검색식 패러다임 (`live-scanner`) 의 첫 신호 (#227 S1). 종목별 임계값 평가 + 거래량 spike 확인의 가장 단순한 진입 룰.

## 패러다임

`docs/specs/live-universe-scanner-paradigm.draft.md` 참조 (별 spec, 정식 승격 전 draft). 본 전략은 universe 의 각 종목마다 매 tick `on_bar(ctx)` 가 호출되어 진입 여부를 평가한다 — 단일 종목 합성 신호가 아니다.

## 진입

- 진입 조건: `RSI(14) < 30` AND `volume[-1] / mean(volume[-21:-1]) > 2.0`
- 신호 형태: `Signal(action="buy", size=default_size, reason="rsi_oversold_volume_spike:rsi=...,vol_ratio=...")`
- 종목별 평가 — universe 380종 (KRX 350 + Binance 30) 각각 독립적으로 신호 트리거 가능

## 진입 크기

- `default_size = 0.05` (5% of equity per entry) — 생성자 인자로 조정 가능
- 포트폴리오 레벨 정합성 (집중도, ENB, CVaR) 은 `risk.evaluate` 가 게이팅
- Phase 7 자본 분배 정책: live-scanner 패러다임 합산 ≤ 30% (production.yaml `capital_allocation.live_scanner_pct`)

## 청산

- **본 전략은 sell signal 을 발행하지 않는다**. 청산은 `LivePositionRiskManager` (#227 S2) 책임.
- `stop_loss_pct = 0.03` — 매수가 대비 -3% 도달 시 시장가 매도
- `take_profit_pct = 0.06` — 매수가 대비 +6% 도달 시 시장가 매도
- `trailing_stop_pct = null` — 비활성 (필요 시 클래스 속성 override)

## 리스크 연동 (#70 mandatory)

```python
from portfolio import AsyncStrategyOrchestrator
from backtest.strategies.live_rsi_oversold_volume_spike import LiveRsiOversoldVolumeSpike

orch.register_strategy("live_rsi_oversold_volume_spike", LiveRsiOversoldVolumeSpike())
orch.register_strategy_returns("live_rsi_oversold_volume_spike", daily_returns_series)
orch.refresh_portfolio_risk()
```

`daily_returns_series` 는 라이브 fill 이벤트로부터 일수익률을 누적해 공급한다 (#194 `PnLAggregator.daily_for(sid)` 활용 예정 — S7 wiring).

## 백테스트

- **5y bench 미실시** — #227 S6 단계에서 KRX universe + Binance universe 양쪽 5y 검증 예정
- Sharpe ≥ 0.5 통과 시 production.yaml 의 `enabled: true` 후보 등록
- 미통과 시 본 spec 의 `status` 를 `rejected` 로 변경 + 본 섹션에 결과 기록

## 운영 규칙

- LLM 호출 금지 (불변식 #6) — RSI / volume MA 모두 결정적 코드로만 산출
- 본 전략은 **stateless across ticks** — 보유 포지션 추적은 `StrategyPositionStore` (#192) + `LivePositionRiskManager` 책임
- 활성화 게이트: `LIVE_SCANNER_ENABLED=1` 환경 변수 + `production.yaml` 에 `enabled: true`
- 단위 테스트: `tests/backtest/test_live_rsi_oversold_volume_spike.py`

## 관련

- `docs/specs/live-universe-scanner-paradigm.draft.md` — 본 패러다임 spec (#227 별 spec, 정식 승격 전)
- [[universe-scan-strategy-pattern]] — 별 패러다임 (cross-sectional weekly rebal). 본 전략과 공존
- [[cs-rsi-div-kr]] — RSI 다이버전스 cross-sectional 변형 (#218)
- 이슈 #227 (Live Universe Scanner 도입)
