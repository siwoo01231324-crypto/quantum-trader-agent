---
type: spec-architecture
id: live-universe-scanner-paradigm
name: Live Universe Scanner Paradigm
title: Live Universe Scanner Paradigm (intraday per-symbol threshold scanning + auto stop/TP)
status: adopted
owner: siwoo
created: 2026-05-11
last_updated: 2026-05-11
tags:
- pattern
- strategy
- live
- intraday
- live-scanner
---

# Live Universe Scanner Paradigm

> **방향 결정 (2026-05-11, #227).** 신규 인트라데이 자동매매 전략은 단일종목 검색식 형태 대신 **유니버스 전체 (KRX 350 + Binance 30) 종목별 임계값 스캔 → 즉시 매수 → 손익비 자동 청산** 패턴을 default 로 한다. universe-scan (주간 리밸) / single-ticker (legacy) 와 함께 **세 패러다임 공존**.

## 한 줄 요약

종목별 임계값 평가 → 신호 트리거 시 즉시 매수 → `LivePositionRiskManager` 가 stop_loss / take_profit / trailing_stop 자동 청산 — 한국 개인투자자 익숙한 **HTS 검색식 + 자동매매 봇** 형태.

## 동기

[[universe-scan-strategy-pattern]] 가 주간 리밸 학술 모멘텀 패턴이라면, live-scanner 는 **개인투자자 검색식 봇** 패러다임:

- universe-scan: "KOSPI 350종 매주 금요일 점수 매겨 top 20 보유" — 펀드매니저 식 cross-sectional momentum
- live-scanner: "RSI<30 + 거래량>2배 인 종목 발견 즉시 매수, -3% 가면 손절, +6% 가면 익절" — HTS 검색식 + 자동매매

두 패러다임 모두 알파 source 가 다름. 공존하면 단/중/장기 시간축 모두 커버 가능. 1인 운용 + 한국 시장 환경에서 검색식 패러다임이 직관적 + 검증/디버깅 쉬움.

## 다른 패러다임과의 차이

| 구분 | universe-scan | **live-scanner** | single-ticker (legacy) |
|---|---|---|---|
| 감시 주기 | 주 1회 (금/일) — batch | **매 tick / 매 분봉** | 매 bar (15분/1h/4h) |
| 진입 판단 | 350종 점수 매겨 상위 N | **종목별 임계값 통과 시 즉시** | 사전 지정 종목 + 단일 시그널 |
| 매도 판단 | 다음 리밸까지 보유 | **손익비 (-3% / +6%) 자동** | 반대 시그널 (예: bearish divergence) |
| 신호 형태 | 바스켓 1 Signal | **종목별 다중 Signal** | 종목별 1 Signal |
| Protocol | AsyncStrategy + cs_* helpers | **AsyncStrategy + LiveScannerMixin** | AsyncStrategy 단독 |
| 적용 자산군 | KRX, Binance 등 | **KRX universe + Binance universe** | BTC / ETH / 005930 등 고정 |
| 예시 | `cs_tsmom_kr_daily`, `breakout_donchian` | `live_rsi_oversold_volume_spike`, `live_macd_bullish_cross_breakout` | `momo_btc_v2`, `momo_kis_v1` |

## 패턴 정의

```
live-scanner 전략 = (
    LiveScannerMixin              # 마커 + stop/TP 클래스 속성
  + on_bar(ctx)                   # per-symbol stateless 평가
  + LivePositionRiskManager       # stop/TP/trailing 자동 청산
  + universe ohlcv cache          # snapshot_builder 가 전 종목 1m bar 누적
  + KIS REST stagger / WS subscribe   # 분당 한도 정합 (350종 동시 호가)
  + risk_integration              # daily_returns_series → orchestrator
)
```

## 필수 컴포넌트

| 컴포넌트 | 책임 | 코드 위치 |
|---|---|---|
| `LiveScannerMixin` | 마커 + stop/TP 클래스 속성 | `src/backtest/strategies/_live_scanner_helpers.py` |
| `live_*` strategy 모듈 | per-symbol on_bar(ctx) → buy Signal (sell 안 함) | `src/backtest/strategies/live_*.py` |
| `LivePositionRiskManager` | stop_loss / take_profit / trailing_stop 자동 청산 | `src/portfolio/live_position_risk.py` |
| Per-symbol dispatch | orchestrator 가 `is_live_scanner` 전략에 종목별 fan-out | `src/portfolio/_async_orchestrator.py::run_bar` |
| Universe ohlcv cache | snapshot_builder 가 전 universe 1m bar 누적 | `src/live/snapshot_builder.py::build_snapshot` |
| Live loop wiring | 매 tick `risk_mgr.evaluate(symbol, last_price, ts)` 호출 | `src/live/loop.py::run_shadow_loop` |
| Env-gated activation | `LIVE_SCANNER_ENABLED=1` + `production.yaml` entry uncomment | `scripts/live_run.py::_run_pipeline` |

## 진입 / 청산 표준 흐름

```
매 tick (KIS 1m polling 또는 Binance WS):
    snapshot = snapshot_builder.build_snapshot(tick)   # 전 universe ohlcv 누적
    intents  = orchestrator.run_bar(ts, snapshot)      # per-symbol dispatch
        → 각 LiveScannerMixin 전략에 universe 모든 종목 ctx 흘림
        → strategy.on_bar(ctx) 가 buy 또는 hold 반환
        → buy 면 OrderIntent (side=buy) 생성
    risk_intents = risk_mgr.evaluate(tick.symbol, last_price, ts)  # stop/TP 체크
        → 보유 (sid, sym) 페어 가격 검사
        → stop_loss / take_profit / trailing_stop 발동 시 OrderIntent (side=sell)
    execute_intents(intents + risk_intents)            # broker 발주
```

## 핵심 결단 (#227 #1~#7)

| # | 결단 | 근거 |
|---|---|---|
| D1 | 별도 `LiveScanStrategy` Protocol 신설 X — 기존 `AsyncStrategy` + `LiveScannerMixin` 마커 | dispatch 분기만 추가 — cs_* / momo_* 회귀 zero |
| D2 | `loop.py` consumer 가 multi-symbol fan-out (single-symbol per tick + universe cache 동시 노출) | 전 universe 동시 처리는 메모리/CPU 부담; tick-by-tick 모델 유지가 결정성 ↑ |
| D3 | Position-level stop/TP 는 strategy 가 아닌 별도 `LivePositionRiskManager` | 진입/청산 책임 분리 = 테스트성 ↑ |
| D5 | 진입 룰 = `_cs_helpers.py` per-symbol 헬퍼 재사용 + threshold gate | cs_* backtest 와 코드 공유 = 일관성 |
| D6 | env-gated activation `LIVE_SCANNER_ENABLED=1` (default OFF) + `production.yaml` 주석 해제 | Phase 별 머지 후에도 운영 영향 zero |

## 신규 검색식 추가 PR 체크리스트

- [ ] `src/backtest/strategies/live_<name>.py` 모듈 — `LiveScannerMixin` 상속, `async def on_bar(ctx) -> Signal | None`
- [ ] `stop_loss_pct` / `take_profit_pct` (필수) + `trailing_stop_pct` (선택) class 속성 명시
- [ ] sell signal 발행 금지 — 청산은 `LivePositionRiskManager` 책임
- [ ] `tests/backtest/test_live_<name>.py` — synthetic OHLCV 로 buy path + warmup + boundary 검증
- [ ] `docs/specs/strategies/live-<name>.md` — 프론트매터 `paradigm: live-scanner`, stop_loss/take_profit/trailing_stop 명시
- [ ] `configs/orchestrator/production.yaml` 에 entry 추가 (commented out — 활성화는 사용자 선택)
- [ ] 5y backtest 검증 (Sharpe ≥ 0.5) 통과 시에만 production `enabled: true` 후보
- [ ] 본 spec 의 Phase 1 strategy 5개 외 신규 추가 시 알파 가설 + 5y bench 결과 [[50-live-universe-scanner-paradigm]] 에 추가

## 활성화 절차

1. `LIVE_SCANNER_ENABLED=1` 환경변수 설정 (`live_run.py` 가 `LivePositionRiskManager` 와이어업)
2. `configs/orchestrator/production.yaml` 의 `live-<name>` entry 주석 해제 (config_loader 가 `register_strategy` 호출)
3. `src/live/conversion.py::SYMBOL_STEP_SIZES` 에 운영 universe 종목 추가 (KRX 6자리 + Binance USDT)
4. 5y backtest 검증 (Sharpe ≥ 0.5) 통과 — 미통과 시 production 미등록

위 4단계 모두 충족시 paper 환경에서 실시간 스캔 + 자동매매 작동.

## 위험

- **검색식 패러다임 알파 검증 부족** — academic literature 약함. 자체 5y backtest 가 모든 것 ([[50-live-universe-scanner-paradigm]] 알파 가설 참조).
- **KIS rate-limit spike** — 350종 매분봉 = 분당 350 호출 → REST stagger 또는 WS subscribe 필수.
- **거래비용 폭증** — intraday 검색식 turnover 매우 높음. KRX 0.55% 라운드트립 × 일 5~10거래 = 일 3~6% 비용. 신호 강도 매우 강해야 net positive.
- **신호 false positive** — 임계값 기반 noise 진입 빈번. 본 패러다임 baseline 5종은 이중 조건 (예: RSI + volume) 으로 false-positive 줄임.

## 정식 승격 (2026-05-11)

본 spec 은 #227 S1~S7 완료 시점에 `live-universe-scanner-paradigm.draft.md` → `live-universe-scanner-paradigm.md` 로 승격. status: `adopted`.

## 관련 노트

- [[universe-scan-strategy-pattern]] — 별 패러다임 (cross-sectional weekly rebal). 본 패러다임과 공존
- [[50-live-universe-scanner-paradigm]] — 본 패러다임 알파 가설 + 5y backtest 계획 (research)
- [[telegram-notifications]] — 청산 시 텔레그램 알림 (#227 S7)
- [[09-system-components]] — 시스템 컴포넌트 맵
- [[42-cross-sectional-momentum-crypto]] — universe-scan paradigm 학술 배경
- 이슈 #227 (본 패러다임 도입)

## 출처

- 본 레포 #227 (Live Universe Scanner — 2026-05-11)
- 본 레포 #218 (Universe-scan strategies — 2026-05-06, 별 패러다임)
- 본 레포 #70 (포트폴리오 리스크 모듈 — 두 패러다임 공통)
