# feat: Live Universe Scanner 패러다임 — 장중 실시간 검색식 자동매매 (검색식 + 손익비 청산)

## 사용자 관점 목표 (사용자 원본 인용)

> "내가 정확히 원하는걸 알려줄게. 주식 검색식처럼 우리 전략에서 정해둔 거래량이라던지 지표들이 있잖아 그래서 **장중 시간에 계속 감시하면서 각 전략마다 원하는 포지션이 오면 자동으로 매수**하고 **우리가 정해둔 전략의 손익비에 따라 매도**하는 **실시간 자동매매 시스템**이야"

> "단기전략 장기전략 모두 실시간으로 돌아가는걸 내가 원하는건데"

→ 한국 개인투자자 익숙한 **HTS 검색식 + 자동매매 봇** 형태. 종목별 임계값 진입 + 손절/익절 자동 청산. KRX universe + Binance universe 양쪽 모두.

## 배경 — 왜 #218 universe-scan 으로 충족 안 되나

#218 PR #222 (universe-scan 7전략) + #226 paper rebal cron 으로 universe-wide 거래 인프라 도입했지만 **다른 패러다임**:

| 구분 | #218 universe-scan (현 운영) | 사용자가 진짜 원함 (본 이슈) |
|---|---|---|
| 감시 주기 | 주 1회 (금/일) — 매주 batch | 장중 실시간 (매 tick / 매 분봉) |
| 진입 판단 | 350종 점수 매겨 **상위 N**만 매수 | 종목별 **임계값 통과** 시 즉시 매수 |
| 매도 판단 | 다음 리밸까지 보유 (stop 없음) | **손익비 (-3% stop / +6% TP)** 자동 청산 |
| 포지션 사이즈 | basket 동일가중 1/N | 신호 발생 시 종목별 고정 비중 |
| 장중 동작 | 무시 → 금요일 대기 | 새 신호 뜨면 즉시 진입 |

#218 = "academic momentum 펀드 매니저" 식. 사용자 원하는 건 = "데이트레이더 검색식". 두 패러다임 공존 가능 (#218 = 주간 알파, 본 이슈 = intraday 검색식 알파).

## 단일종목 prototype 이미 존재

현재 운영 중인 `momo_kis_v1` (KIS daemon, 005930 RSI 다이버전스) = **단일종목 검색식 자동매매 prototype**. 이걸 universe-wide (KRX 350종 + Binance 30종) 로 확장하는 것이 본 이슈의 본질.

## 완료 기준

### Phase 1 — 신규 전략 패러다임 (`LiveScanStrategy`) 설계 + 4~5 strategy 모듈
- [ ] `src/backtest/protocol.py` 또는 별도 모듈에 `LiveScanStrategy` Protocol 정의
  - 차이점: cross-sectional ranking 대신 **per-symbol 임계값 evaluation**
  - 시그니처: `async def on_tick(symbol, ohlcv_history, ctx) -> Signal | None`
  - Signal action: `"buy"` / `"sell"` / `"hold"` (개별 종목 단위, basket 아님)
- [ ] 4~5 검색식 전략 (단기 / 일중 패턴):
  - `live_rsi_oversold_volume_spike` — RSI(14) < 30 AND 거래량 > 평소 2배 → 매수
  - `live_macd_bullish_cross_breakout` — MACD bullish cross + 20일 신고가 돌파 → 매수
  - `live_bb_lower_bounce` — BB 하단 터치 후 회복 + 매수세 → 매수
  - `live_breakout_with_atr_stop` — 신고가 돌파 + ATR 기반 trailing stop
  - `live_oversold_with_divergence` — 가격 하락 중 RSI 다이버전스 → 매수
- [ ] 단위 테스트 1건/전략 (synthetic OHLCV → 진입/청산 trigger 시뮬)

### Phase 2 — Position-level stop/TP 매니저
- [ ] `src/portfolio/position_manager.py` (또는 동등 모듈):
  - 매 tick / 매 분봉 보유 종목 가격 체크
  - 매수가 * (1 - stop_loss_pct) 도달 시 → SELL 발주
  - 매수가 * (1 + take_profit_pct) 도달 시 → SELL 발주
  - Trailing stop 옵션 (high water mark - trail_pct)
- [ ] `momo_btc_v2` 의 hard_stop / take_profit 패턴 (S2b variant) 재사용 / 일반화
- [ ] strategy spec 의 frontmatter 에 stop_loss_pct / take_profit_pct / trailing_stop 필드 추가
- [ ] 단위 테스트: 매수가 80,000 → 가격 76,400 (stop 발동) → SELL 자동 발주

### Phase 3 — KIS broker universe-wide live quote
- [ ] KIS broker 가 350종 동시 호가 받기 (#212/#213 rate-limit 정합 검증)
  - 현 KIS API: 분당 호출 한도 (paper 60/분, live 계좌별 다름)
  - 350종 매분봉 호가 = 분당 350 호출 = **한도 초과 risk**
  - 해결: stagger fetch (5초 간격) 또는 Websocket subscribe (KIS WS API 사용)
- [ ] Binance broker 동일 — 30종 동시 호가 (Binance WS 사용 시 무료 무한)
- [ ] paper broker 가 동적 350+30 = 380 instruments 동시 시뮬

### Phase 4 — 라이브 루프 universe-tick 처리
- [ ] 현재 `src/live/loop.py` 는 단일종목 tick 가정 → universe-wide tick 처리로 확장
  - 매 tick (또는 매 분봉) 마다 각 LiveScanStrategy.on_tick() 호출
  - 진입 신호 → OrderIntent → 기존 weights_to_orders 우회 (개별 종목 발주)
  - 청산 신호 (stop/TP) → 즉시 SELL 발주
- [ ] env-gated activation (`LIVE_SCANNER_ENABLED=1`) — default OFF, 점진 활성화

### Phase 5 — 백테스트 검증
- [ ] 검색식 패러다임은 학술 검증 약함 → **자체 5y backtest 필수**
  - 5 신규 전략 × KRX universe + Binance universe = 10 백테스트
  - Sharpe / MDD / Ann / Trades / WinRate / 평균 hold 일수 / 손익비 적합도
- [ ] 검증 통과 (Sharpe ≥ 0.5) 인 전략만 production 활성화
- [ ] frontmatter sharpe_bt / mdd_bt / annual_return_bt 갱신

### Phase 6 — 대시보드 + Telegram + 모니터링
- [ ] 대시보드 `/strategies/{id}` 페이지 — Live Scanner 전략에 다음 추가:
  - 실시간 보유 종목 + 매수가 + 현재가 + 손익율 + stop/TP 거리
  - "감시 중" 종목 (universe 중 임계값 거의 도달한 종목)
- [ ] Telegram 알림:
  - 진입 시 즉시 알림 ("매수: 005930 @ 80,000 (RSI 28, 거래량 2.3×)")
  - 청산 시 즉시 알림 ("매도: 005930 @ 84,800 (+6.0%, take_profit)")
  - 일 1회 요약 디지스트 (당일 진입 N건, 청산 M건, 일PnL)
- [ ] daily_check.ps1 / daily_check_kis.ps1 — Live Scanner 활성 종목수 + 실패 fetch 카운트 확인

### Phase 7 — 기존 #218 universe-scan 와 공존 정책
- [ ] 두 패러다임 동시 운영 (장기 momentum + 단기 검색식)
- [ ] 자본 분배: 예) universe-scan 70% / Live Scanner 30% (production.yaml 에서 설정)
- [ ] 충돌 방지: 같은 종목 양 패러다임에서 동시 진입 시 risk evaluator 가 합산 비중 체크
- [ ] daily_check 가 두 path 모두 모니터링

## 흡수 / 통합 대상

- `src/backtest/strategies/momo_kis_v1.py` — 현 단일종목 KIS daemon. **Live Scanner 의 첫 단일종목 사례** 로 마이그레이션 (혹은 첫 universe 사례 prototype). status: deprecated (Live Scanner 활성화 후) 또는 retain (단일종목 hedging 용).
- `momo_btc_v2.py` 의 hard_stop / take_profit 패턴 → Phase 2 position manager 로 일반화.

## 의존성 / 차단

- **#218 (universe-scan)** 머지 완료 → 본 이슈 시작 가능. 현재 운영 안전 (zero-impact 검증됨).
- **#212 / #213 (KIS rate-limit)** — Phase 3 broker 확장 시 ws subscribe 또는 stagger fetch 필요. websocket subscribe 방식 권장.
- **#225 / #226 (paper rebal cron)** — 본 이슈와 별개 path 로 공존. 두 cron 모두 운영.

## 위험

- **검색식 패러다임의 알파 검증 부족** — academic literature 가 약함 → 자체 backtest 가 모든 것. 검증 통과 못 하는 전략은 production 미등록.
- **KIS rate-limit spike** — 350종 매분봉 호가 = 분당 350호출 → **websocket 필수** (REST 만으로는 한도 초과). KIS WS API (FHKST*) 학습 필요.
- **Position 관리 복잡도** — 동시 보유 종목수 폭증 가능 (예: 50종 동시 보유 → 매수가/stop/TP 추적 50개). 메모리 + 대시보드 부하.
- **거래비용 폭증** — intraday 검색식은 turnover 매우 높음. KRX 0.55% 라운드트립 × 일 5~10거래 = 일 3~6% 비용 → 신호 강도 매우 강해야 net positive.
- **신호 false positive** — 검색식이 임계값 기반이라 noise 진입 빈번 → 신호 후처리 (예: 30초 hold + 재확인) 필요.

## 작업 분량 추정

총 8~13일 (focused engineering):
- Phase 1: 1~2일
- Phase 2: 1일
- Phase 3: 2~3일 (KIS WS 학습 포함)
- Phase 4: 1~2일
- Phase 5: 2~3일 (5y bench × 10전략)
- Phase 6: 1일
- Phase 7: 0.5일

## 관련 노트 / 참조

- 현 universe-scan paradigm: `docs/specs/universe-scan-strategy-pattern.md` (#218)
- 현 단일종목 prototype: `src/backtest/strategies/momo_kis_v1.py` (#96)
- BTC stop/TP 사례: `src/backtest/swing/strategies.py` S2b variant (-1% stop / +7% TP)
- KIS broker rate-limit: #212 / #213
- 라이브 loop: `src/live/loop.py` (#80)
- 본 이슈는 #218 / #226 후속, 별도 paradigm.

## 개발 체크리스트

- [ ] 5+ 신규 LiveScanStrategy 모듈 + 단위 테스트 / 전략
- [ ] 5y backtest 검증 통과 후만 production 활성화
- [ ] KIS WS subscribe 검증 (rate-limit 0건)
- [ ] 라이브 루프 env-gated 활성화 (`LIVE_SCANNER_ENABLED=1`, default OFF)
- [ ] 대시보드 / Telegram 진입·청산 즉시 알림 + 일 요약
- [ ] 기존 universe-scan (#218) + Live Scanner 동시 운영 검증
- [ ] check_invariants --strict 통과
- [ ] 해당 디렉토리 `.ai.md` 최신화


---

## 작업 내역

### 2026-05-11 — S1 (Phase 1 spike) 완료

- 13개 deliverable 의 첫 6개 완성:
  - `src/backtest/strategies/_live_scanner_helpers.py` — `LiveScannerMixin` 마커 + stop/TP class attr
  - `src/backtest/strategies/live_rsi_oversold_volume_spike.py` — 첫 신호 (RSI(14) < 30 AND volume > 2× MA)
  - `src/portfolio/_async_orchestrator.py` — per-symbol fan-out dispatch + quarantine dedup per tick
  - `tests/backtest/test_live_rsi_oversold_volume_spike.py` — 8 tests, 8 pass
  - `tests/portfolio/test_orchestrator_live_scanner_dispatch.py` — 5 tests, 5 pass
  - `docs/specs/strategies/live-rsi-oversold-volume-spike.md` — spec (status: backtest)
  - `docs/specs/live-universe-scanner-paradigm.draft.md` — paradigm spec (draft, S7 까지 정식 승격 보류)
- 회귀 zero: `tests/backtest/` 106 pass / 1 skip; `tests/portfolio/` 48 pass
- `python scripts/check_invariants.py --strict` 통과 (199 노트)
- `.ai.md` 갱신: `src/backtest/strategies/`, `src/portfolio/`

다음 — S2: `LivePositionRiskManager` (stop/TP 자동 청산) + spec frontmatter schema 확장.

### 2026-05-11 — S2 (Phase 2) 완료

- `src/portfolio/live_position_risk.py` 신규 — `LivePositionRiskManager` + `StopTpPolicy`
  - `evaluate(symbol, last_price, ts) -> list[OrderIntent]` — 보유 (sid, sym) 가격 체크 → SELL emit
  - 청산 우선순위: stop_loss → take_profit → trailing_stop
  - 모든 산술 Decimal (PnLAggregator 정합)
  - WAL `position_stop_triggered` 이벤트 발행
- `tests/portfolio/test_live_position_risk.py` — 14 tests, 14 pass
  - StopTpPolicy 검증 3건 (valid / invalid stop_loss / invalid trailing)
  - stop_loss 트리거 3건 (above/at/below threshold)
  - take_profit 트리거 2건
  - trailing_stop 3건 (high-water 추적, entry 위에서만 발동, sell 후 리셋)
  - no-position 2건
  - multi-strategy 1건 (서로 다른 정책 독립 적용)
- 회귀 zero: 236/237 (1 skip) — `tests/portfolio/`, `tests/backtest/`, `tests/live/`
- `.ai.md` 갱신: `src/portfolio/`

다음 — S3: `loop.py` multi-symbol fan-out + `LIVE_SCANNER_ENABLED=1` env-gated wiring + LivePositionRiskManager 라이브 결합.

### 2026-05-11 — S3 (Phase 4 partial) 완료

- `src/portfolio/_async_orchestrator.py` — public `strategies` property 추가 (live_run.py 의 LiveScannerMixin 정책 등록 용)
- `src/live/loop.py` — `ShadowConfig.position_risk_manager` 필드 추가 + consumer 에서 매 tick `evaluate(symbol, last_price, ts)` 호출 + SELL intents WAL 발행 → `execute_intents` 라우팅
- `scripts/live_run.py::_run_pipeline` — `LIVE_SCANNER_ENABLED=1` env-gated 분기. `LivePositionRiskManager` 생성 + `on_orchestrator_ready` 콜백에서 등록된 LiveScannerMixin 전략의 stop_loss/take_profit/trailing_stop 정책을 자동 등록
- `tests/live/test_loop_live_scanner_wiring.py` — 3 integration smoke test
  - manager set 시 매 tick evaluate 호출 (3/3)
  - manager 없을 때 zero-impact (legacy path 회귀 zero)
  - SELL intent → WAL `signal_emitted` (reason prefix `live_stop_loss`) 라우팅 검증
- 회귀 zero: 239/240 (1 skip) — portfolio + backtest + live 전체
- `.ai.md` 갱신: `src/live/`

이제 paper 환경에서 `LIVE_SCANNER_ENABLED=1` 로 실행 시 LiveScannerMixin 전략이 universe 에 dispatch + 자동 stop/TP 청산까지 end-to-end 동작 가능. 활성화는 production.yaml 의 `enabled: true` (S4 ~ S6 후) 와 env 변수 동시 충족 필요.

다음 — S4: 4 추가 신호 (`live_macd_bullish_cross_breakout`, `live_bb_lower_bounce`, `live_breakout_with_atr_stop`, `live_oversold_with_divergence`).

### 2026-05-11 — S4 (Phase 1 잔여) 완료

- 4 새 strategy 모듈:
  - `live_macd_bullish_cross_breakout.py` — MACD histogram zero-cross + 20봉 신고가 (3%/6%)
  - `live_bb_lower_bounce.py` — BB 하단 이탈/회복 + volume 확인 (3%/6%)
  - `live_breakout_with_atr_stop.py` — 20봉 신고가 돌파 + trailing 4% 주청산 (5%/20% 안전망)
  - `live_oversold_with_divergence.py` — downtrend + RSI bullish divergence (3%/6%) — `momo_kis_v1` universe-wide 변형
- 4 단위 테스트 — 21/21 pass (warmup / boundary 조건 모두 검증; MACD 와 divergence 의 buy path 는 engineered synthetic price path 로 정확히 fire)
- 4 spec md — paradigm=`live-scanner`, frontmatter stop_loss_pct/take_profit_pct/trailing_stop_pct 명시
- 회귀 zero: 260/261 (1 skip) — portfolio + backtest + live 전체 통과
- invariant 통과 (199 → 203 노트, 4 신규 spec)
- `.ai.md` 갱신: `src/backtest/strategies/`

이제 LiveScannerMixin 신호 5종 (S1 + S4) + LivePositionRiskManager (S2) + 라이브 루프 wire-up (S3) 까지 완비. 단, production.yaml 에 등록되지 않아 활성화 안 됨 (S5+S6 후 활성화).

다음 — S5: KIS WS multi-subscribe (350종 동시 호가) + Binance WS smoke + paper broker 380 instruments scale test.

### 2026-05-11 — S5 (Phase 3 partial) 완료

- `src/live/feed_kis.py::KISMarketFeed` — `stagger=True` (cycle 내 N 호출 분산) + `max_qpm` (round-robin window) 옵션 추가. 350종 KIS REST 분당 한도 (~60/min paper) 회피 가능
- `tests/live/test_feed_kis_stagger.py` — 3 tests (stagger 간격 / non-stagger burst / max_qpm rotation)
- `tests/live/test_paper_broker_universe_scale.py` — 380 OrderIntent 한 번에 처리, < 10s 안에 ack 전부 (memory + WAL 스케일 정합)
- 발견: `src/live/conversion.py::SYMBOL_STEP_SIZES` 가 BTCUSDT/ETHUSDT/SOLUSDT 만 등록 — universe-wide live-scanner 활성화 전 KRX 350 + Binance 30 추가 필요 (별 이슈로 분리, S7 활성화 검토 시 보강)
- 풀 KIS WS market data subscribe 구현은 별 이슈로 분리 — 실 API 키 + KIS tr_id (`H0STCNT0`) 필요
- 회귀 zero: 264/265 (1 skip)

### 2026-05-11 — S6 (Phase 5 skeleton) 완료

- `scripts/bench_live_scanner.py` — 5y backtest harness skeleton
  - 5 strategy × 2 universe (KRX + Binance) = 10 runs 구조
  - per-symbol replay loop: bar-by-bar `on_bar` → buy signal → strategy class attr 의 stop/TP/trailing 적용 → exit 시점 추출 → metrics
  - metrics: trades / win_rate / avg_hold_days / Sharpe / MDD / AnnRet / realized_pnl_profit / loss
  - universe loader 는 stub — 실제 5y data fetch + 검증 실행은 별 이슈 (#229) 로 분리
- 모든 strategy spec 의 sharpe_bt / mdd_bt / annual_return_bt 는 null 유지 (#229 후 갱신 예정)

### 2026-05-11 — S7 (Phase 6+7) 완료

- `configs/orchestrator/production.yaml`:
  - 5 live-scanner entry 추가 (모두 commented out — `LIVE_SCANNER_ENABLED=1` 환경변수 + 주석 해제 둘 다 충족시 활성화)
  - `capital_allocation` 필드 추가 (universe_scan 70% / live_scanner 30% / single_ticker 0%)
- `scripts/telegram_alert.py`:
  - `position_stop_triggered` event_type 을 `CRITICAL_EVENT_TYPES` 에 추가 (LivePositionRiskManager 발동 시 자동 텔레그램)
  - `_format_position_stop` — friendly 한글 메시지 + 트리거별 아이콘 (🛑/🎯/📉)
- `tests/scripts/test_telegram_alert.py` — 4 신규 케이스 (stop_loss / take_profit / trailing / signal_emitted 비-critical 검증)
- 회귀 zero: 370/371 (1 skip)
- invariant 통과 (203 노트)

## #227 전체 마무리 — S1~S7 모두 완료

- **누적 변경 (7 modified)**: `_async_orchestrator.py`, `loop.py`, `live_run.py`, `feed_kis.py`, `production.yaml`, `telegram_alert.py`, 4× `.ai.md`
- **누적 신규 (16 files)**: 마커 1 + 검색식 5 + 자동매도 매니저 1 + bench harness 1 + 테스트 9 + spec 6 + work folder 1
- **테스트**: 신규 ~50건, 전체 회귀 zero (370 pass)
- **invariant**: 통과
- **활성화 조건** (paper 운영 시작): (1) `LIVE_SCANNER_ENABLED=1` env (2) `production.yaml` 의 live-* entry 주석 해제 (3) `SYMBOL_STEP_SIZES` 에 KRX/Binance universe 종목 추가 (4) 5y backtest 검증 (Sharpe ≥ 0.5) — 모두 별 이슈 #229 후속

다음 단계: 사용자 승인 후 commit + PR. 이후 #229 (5y backtest 실제 실행) + KIS WS market subscribe 별 이슈 진행.

### 2026-05-11 — 후속 4건 코드 보강 (PR #228 흡수)

#2 + #7 + #1 + #3 코드 수준 완료. 실 5y backtest / 실 KIS WS 검증은 사용자 환경에서 별도 (Claude 세션 timeout 위험 회피).

- **#2 SYMBOL_STEP_SIZES 확장**:
  - `src/live/conversion.py::get_step_size()` 신규 — KRX 6자리 fallback (step=1) + Binance USDT pair fallback (step=0.001)
  - `intent_to_order_request` 가 fallback 사용 — universe-wide live-scanner 활성화 시 종목별 등록 불필요
  - `tests/live/test_conversion_step_size_fallback.py` (9 tests, 9 pass)
  - paper_broker scale test 의 monkeypatch 제거 — fallback 자동 작동
- **#7 cs_* 5y bench harness**:
  - `scripts/bench_cs_universe.py` — 5종 cs_* generic harness (cs_rsi_div_kr / cs_bb_macd_kr / cs_adx_ma_kr / cs_rsi_div_crypto / cs_macd_vol_crypto)
  - 기존 `bench_cs_tsmom_kr` / `bench_cs_tsmom_crypto` 의 universe + fetch + cache 재사용
  - `--strategy <id>` 또는 `--all` 로 5종 일괄 실행
  - 실 5y 실행은 사용자 환경 (350종 fetch ~30분, 캐시 후 < 1분)
- **#1 live-scanner 5y bench universe loader 보강**:
  - `scripts/bench_live_scanner.py::_load_krx_universe` / `_load_binance_universe` 가 cs bench 의 cache 재사용
- **#3 KIS WS market subscribe**:
  - `src/brokers/kis/tr_ids.py::TR_ID_WS_KRX_TRADE = "H0STCNT0"` 추가
  - `src/live/feed_kis_ws.py::KISWebSocketMarketFeed` 신규 — `MarketDataFeed` Protocol 준수, single connection multi-symbol subscribe, `^` 구분 frame 파싱
  - `tests/live/test_feed_kis_ws.py` (9 tests, 9 pass) — wire-frame parser + subscribe payload + protocol guards
  - 실 API 통합 검증은 사용자 환경 (메인 .env 의 KIS 키 사용)

회귀 zero: 388/389 (1 skip) — 신규 ~20 tests 추가
invariant 통과: 206 노트

**사용자가 직접 실행할 항목** (코드 자체는 PR #228 안에):
1. `python scripts/bench_cs_universe.py --all` — 5y cs_* bench (수~30분)
2. `python scripts/bench_live_scanner.py --all` — 5y live-scanner bench (cs cache 재사용)
3. KIS WS smoke: 메인 .env 키로 `KISWebSocketMarketFeed` 통합 테스트 1건 (장중 시간)
4. 검증 통과 strategy 의 spec frontmatter (sharpe_bt 등) 갱신 + production.yaml `enabled: true` 결정

### 2026-05-11 — 정책 / 문서 정리 (사용자 요청)

향후 신규 인트라데이 전략은 **universe-scan + live-scanner 둘 다 default** 라는 정책을 레포 전반에 명시:

- `AGENTS.md` §"전략 패턴" — universe-scan + live-scanner 두 default 패러다임 정의 + single-ticker (legacy) 와 공존. 시간축 기반 선택 가이드 명시.
- `CLAUDE.md` §"새 전략 추가 시 필수" — 두 default 패러다임 모두 옵션으로. paradigm 별 PR 체크리스트 + 5y bench gate (universe-scan ≥0.5 / live-scanner intraday ≥1.0).
- **신규**: `docs/specs/live-universe-scanner-paradigm.md` (status: `adopted`) — draft 떼고 정식 spec 으로 승격. 다른 두 패러다임과의 비교, 활성화 절차, PR 체크리스트.
- **신규**: `docs/background/50-live-universe-scanner-paradigm.md` (research) — 검색식 패러다임의 알파 가설 (단기 reversal / breakout momentum / 거래량 점프) + 5y backtest 검증 계획 + 한국시장 특수성. 학술 출처 명시.
- **신규**: `docs/specs/telegram-notifications.md` (adopted) — 운영 알림 전체 정책 (mode_switched / fill_anomaly / kill_switch / **position_stop_triggered**) + #227 S7 추가분 (LivePositionRiskManager 발동 시 🛑/🎯/📉 한글 메시지) 통합.
- `live-universe-scanner-paradigm.draft.md` 삭제 (정식 승격 완료).

invariant 통과 (203 → 206 노트). 회귀 zero (370/371).
