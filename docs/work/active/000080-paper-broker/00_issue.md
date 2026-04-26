# feat: 라이브 실행 프레임워크 (PaperBroker + Phase 1 Shadow Paper)

## 목표
[[29-paper-to-live-protocol]] 의 **Phase 1 Shadow Paper** 를 구현. 실시간 WebSocket 시세 → 오케스트레이터 (#78) → 가상 체결 (PaperBroker) → 결과 기록/관측 까지의 end-to-end 라이브 루프 (실자금 미투입).

## 배경

### 공백
- 레포에 **실시간 이벤트 루프가 없음**. `src/backtest/` 는 historical bar replay 전용.
- [[09-system-components]] §5 MVP 수용 기준 "(a) WS 단절 후 자동 재연결, (c) 페이퍼 체결 후 포지션·PnL 일치" 가 구현체 없이 문서만 존재.
- `src/brokers/binance/ws.py`, `src/brokers/kis/` 커넥터는 #68 머지됐으나 **오케스트레이터에 미배선**.
- `docs/specs/execution-algorithms.md` §5 `MockMatchingEngine` 스펙 있으나 구현체 없음.

### 왜 Phase 1 부터
[[29-paper-to-live-protocol]] §2 의 4단계 프레임워크 중:
- Phase 0 (백테스트 승인) — #67 이슈로 기초 가능
- **Phase 1 (Shadow Paper)** — 본 이슈 대상
- Phase 2+ (Live Paper · Pilot · Production) — 본 이슈 머지 후 별도 이슈. 각각 **4~12주 실측** 필요.

Phase 1 을 먼저 하는 이유 = [[29-paper-to-live-protocol]] §9 "단계 건너뛰기 금지" 불변식. 실제 브로커 API 연결 전에 **이벤트 루프 결함** 을 먼저 드러낸다.

## 범위

### 신규 모듈
- `src/live/loop.py` — 이벤트 루프 (asyncio). 마켓 틱 → 오케스트레이터.run_bar → 결과 처리.
- `src/live/reconnect.py` — WS heartbeat + 지수 backoff + REST 스냅샷 보간.
- `src/execution/paper_broker.py` — PaperBroker 어댑터. 호가창 스냅샷 기반 slippage 시뮬 체결.
- `src/execution/mock_matching.py` — `MockMatchingEngine` (execution-algorithms §5 스펙 구현).
- `src/live/.ai.md` / `src/execution/.ai.md` 신규 or 갱신.

### 관측
- [[observability]] 메트릭 10종 송출 경로 연결:
  - `qta_ws_disconnect_total`, `qta_ws_lag_ms`, `qta_tick_gap_total`
  - `qta_paper_fills_total`, `qta_paper_pnl_krw`
  - `qta_risk_breach_total{rule_id}` (#70 이 정의한 라벨 공간)
  - `qta_order_ack_latency_ms` 등
- [[kill-switch-dr]] 자동 트리거 3종 테스트:
  - DD 한도 초과 → kill-switch trip
  - API 오류율 임계 초과 → kill-switch trip
  - 이상 체결 패턴 (1초 내 동일 심볼 5건) → kill-switch trip

### Exit Criteria (Phase 1 승격 조건 — [[29-paper-to-live-protocol]] §3.3)
- [ ] WebSocket 단절 자동 재연결 정상 (≥ 1회 검증)
- [ ] 시세 lag > 500ms 발생률 < 5%
- [ ] 모든 체결이 PaperBroker 로그에 남음 (누락 0)
- [ ] 백테스트 Sharpe vs Shadow Paper Sharpe 차이 ≤ 0.3 (비용 포함)
- [ ] kill-switch 자동 트리거 3종 테스트 통과

### 롤백 트리거 (§3.4)
- WebSocket 재연결 실패 2회 이상
- 체결 누락 1건 이상
- Sharpe 괴리 > 0.5

## 완료 기준
- [ ] `src/live/loop.py` + `src/execution/paper_broker.py` 구현
- [ ] 2+ 전략 (#79 카탈로그 확장 이후 3전략)으로 **최소 10 거래일 shadow 운영 로그**
- [ ] 주문 건수 N ≥ 30 기록
- [ ] Exit Criteria 5개 항목 모두 문서화된 증거 (logs + 리포트)
- [ ] 롤백 트리거 3종 수동 injection 테스트 통과
- [ ] `docs/work/active/000<본 이슈>/02_implementation.md` 에 Shadow 운영 리포트

## 의존성
- **#73** (브로커 어댑터 async) — asyncio 이벤트 루프와 어댑터 간 I/O 경로 호환 필수. **Merged 필요.**
- **#78** (멀티 전략 오케스트레이터) — `run_bar` 가 본 이벤트 루프의 tick 단위 엔트리. **Merged 필요.**
- **#70** (포트폴리오 리스크) — `Snapshot.portfolio_risk` 갱신 경로. **Merged 필요.**
- **#69** (포지션 사이징) — 오케스트레이터 내부 소비자. Merged 권장. 없으면 dummy sizer.
- 간접 의존: **#79** (전략 카탈로그) — Phase 1 검증 의미를 위해 2+ 전략 필요. #79 없으면 단일 `momo-btc-v2` 로만 실행 (통계 의미 약함).

## 참고 research
- [[29-paper-to-live-protocol]] §3 Phase 1 (정의·기간·exit criteria·롤백 트리거), §9 (자주 발생하는 실수 — 단계 건너뛰기·Sharpe 착시·LLM 자동화 과신)
- [[09-system-components]] §5 Phase 1 MVP 수용 기준, §3 FMEA (F1 WS 단절, F6 리스크 초과, F8 주문-체결 불일치)
- [[execution-algorithms]] §5 MockMatchingEngine, §3.1 슬리피지 모델 (SquareRootImpact)
- [[observability]] — 본 이슈 산출 메트릭 규격
- [[kill-switch-dr]], [[kill-switch-runbook]] — Phase 1 에서 3종 자동 트리거 검증
- [[risk-rule-dsl]] §7.1 rule_id 라벨 공간 — 본 이슈가 breach 로그 consumer
- [[10-broker-api-comparison]] — 어떤 WS 를 Phase 1 소스로 쓸지 (Binance public 권장: 키 불요)

## 주의사항
- **LLM 이 라이브 결정에 직접 개입 금지** (CLAUDE.md 불변식 #6 + [[29-paper-to-live-protocol]] §8.3)
- **Idempotency-key** — PaperBroker 에서도 실제 브로커와 동일 경로 ([[09-system-components]] §6 불변식 #2)
- **Write-ahead log** — 주문/체결 이벤트는 append-only JSONL 에 먼저 쓰고 메모리 반영
- **Single-process lock** — 중복 실행 방지 (FMEA F9, 분산 락 or 파일 락)
- 본 이슈는 **가상 체결만**. Phase 2 (KIS 모의계좌 실제 API) 는 별도 이슈로 분리.

## 후속 (out of scope)
- Phase 2 Live Paper (브로커 모의계좌 실제 API) — 별도 이슈 (4주 실측 필요)
- Phase 3 Live Pilot (실자금 5%) — 별도 이슈 (8주 실측 + 승인 2인)
- Phase 4 Full Production (M1~M5 스케일업) — §6 스케일업 트랙 별도 이슈
- 실시간 feature store 캐시 / 온라인 추론 — [[09-system-components]] §2 FeatureStore 확장
- DCC-GARCH 시변 상관 ([[19-portfolio-risk]] §2.3) — v3

---

## 특허 리서치 (#84) 보강

라이브 실행 프레임워크(특히 paper trading + 실행 알고 + TCA) 범위에 특허 리서치에서 도출된 **4 개 차용 제안** 이 연관된다. 본 이슈 구현 시 함께 고려.

### 1. VWAP 볼륨 프로파일 실시간 blend
- **출처**: `docs/background/34-patents-execution-algos.md` §2 💎 제안
- **특허 참고**: Goldman Sachs US8571967B1 (2026-12 만료 예정)
- **내용**:

**제안 이름**: 실시간 당일 체결량 피드백으로 VWAP 볼륨 프로파일 자동 재계산

**적용 대상 파일/함수**: `src/execution/vwap.py::VWAPAlgo._emit_next()` 및 `VWAPAlgo.__init__(volume_profile)`

**접목 방법**: 현재 `VWAPAlgo`는 초기화 시 `volume_profile: list[float]`를 정적으로 주입받아 전체 실행 기간 동안 고정 비율로 사용한다. 특허 (a)+(b) 구성요소에서 착안하여, 실행 중 `on_market_tick(tick)` 콜백에서 누적 체결량(cumulative_volume)을 받아 남은 슬라이스의 비율 벡터를 Bayesian update 방식으로 재계산하는 `live_volume_updater` 매개변수를 추가할 수 있다. 구체적으로 `VWAPAlgo.on_market_tick(tick)` 시그니처에 `realized_volume: int` 파라미터를 추가하고, 남은 슬라이스의 `weights[idx:]`를 `(역사적 비율 × α) + (당일 실시간 비율 × (1-α))`로 블렌딩한다(`α`는 `algo_params["vwap_alpha"]`로 설정). 이는 특허 (a)(b) 구성요소를 **수식·파라미터를 다르게 구현하여** 회피하면서도 실질적 성능 개선을 달성한다.

**기대 효과**: 장중 예상치 못한 거래량 스파이크(VI 발동, 공시 전후) 시 남은 슬라이스를 자동으로 재분배하여 실제 VWAP 벤치마크 대비 추적 오차(tracking error)를 줄인다. KRX 환경에서 동시호가·VI 발동 구간([[07-market-microstructure-basics]] §4)에 특히 효과적.

**저비용 검증 경로**: `tests/test_vwap_live_update.py`에서 고정 profile vs. 동적 갱신 비교 백테스트 — 슬리피지 감소율 측정.

---

### 2. OrderRouter 비용 기반 동적 라우팅
- **출처**: `docs/background/34-patents-execution-algos.md` §3 💎 제안
- **특허 참고**: CME Group US11164248B2 (활성)
- **내용**:

**제안 이름**: 브로커별 실시간 레이턴시·수수료 추정치 기반 최적 라우팅 점수 도입

**적용 대상 파일/함수**: `src/brokers/router.py::OrderRouter.place_order()` 및 신규 `src/brokers/router.py::ExecutionCostEstimator`

**접목 방법**: 특허 (b) 구성요소의 "best execution platform" 선택 개념을 착안으로, `OrderRouter`에 `ExecutionCostEstimator` 헬퍼 클래스를 추가한다. 이 클래스는 최근 N건 `BrokerFill`에서 `(fill.price - mid_price) / mid_price` 슬리피지와 `fill.fee`를 집계하여 브로커별 `execution_cost_score`를 산출한다. `OrderRouter.place_order()` 진입 시 `KIS` vs `Binance` 등 다중 브로커가 등록된 경우 점수가 낮은 브로커로 자동 라우팅한다. `algo_params["force_broker"]` 오버라이드로 전략이 특정 브로커를 강제 지정 가능.

이는 특허 (a)(이중 매칭 엔진) 구성요소를 우리가 채택하지 않고, (b)의 "가격·수량 기반 라우팅"만을 **단일 브로커 스왑 프레임워크**로 재구성하여 침해 구성요건 전체 충족을 회피한다.

**기대 효과**: KIS fallback 전환([[10-broker-api-comparison]] §5) 로직이 단순 장애 기반에서 **실행 비용 최적화 기반**으로 격상된다. 저유동성 구간에서 Binance Futures 대비 KIS 슬리피지가 높아질 때 자동으로 라우팅 비율 조정 가능.

**저비용 검증 경로**: `tests/test_order_router_cost.py`에서 mock 브로커 2개로 슬리피지 차이 시나리오 → 라우팅 선택 검증.

---

### 3. TWAP 볼라틸리티 레짐 적응 + KRX VI 게이트
- **출처**: `docs/background/34-patents-execution-algos.md` §4 💎 제안
- **특허 참고**: Roman Ginis US20210272201A1 (심사 중) — ML 로직은 회피, 규칙 기반으로 단순화
- **내용**:

**제안 이름**: 변동성 레짐 기반 TWAP 슬라이스 간격 동적 조정 (`volatility_adaptive_twap`)

**적용 대상 파일/함수**: `src/execution/twap.py::TWAPAlgo._maybe_emit()` 및 `TWAPAlgo.__init__()`

**접목 방법**: 특허 (d) 구성요소의 "볼라틸리티 레짐에 따른 매칭 빈도 조정" 개념을 착안으로, 현재 `TWAPAlgo`가 `duration / slice_count`로 균등 분할하는 방식에 `volatility_weight: list[float]` 선택 파라미터를 추가한다. 이 파라미터는 `on_market_tick` 시점의 실현 변동성(예: 최근 5 tick bid-ask spread 평균)을 기반으로 외부에서 계산·주입된다. 변동성이 낮은 구간에는 슬라이스를 조기에 집중 실행하고, 변동성이 높은 구간(VI 발동 직후 등)에는 슬라이스를 지연함으로써 시장충격을 줄인다. ML 모델 의존 없이 단순 규칙 기반으로 구현하여 특허 (b) ML 엔진 구성요소를 의도적으로 배제한다.

**기대 효과**: KRX VI 발동([[07-market-microstructure-basics]] §4-2) 직후 단일가 전환 구간에서 TWAP 슬라이스 발송을 자동 일시 정지하고 접속매매 재개 후 재개. 현재 `TWAPAlgo`는 이 동작이 없어 단일가 구간에서 불필요한 IOC 주문이 발생할 수 있음.

**저비용 검증 경로**: `src/execution/krx_handler.py` 이벤트(VI 발동, 서킷브레이커)를 TWAP 실행 루프에 연결 → VI 발동 시나리오 백테스트에서 슬리피지 개선율 측정.

---

### 4. Implementation Shortfall 사전 추정 + TCA 메트릭
- **출처**: `docs/background/34-patents-execution-algos.md` §5 💎 제안
- **특허 참고**: BlackRock US12067619B1 (활성) — 라우팅 로직은 회피, **측정·로깅만 채택**
- **내용**:

**제안 이름**: 주문 발송 전 IS(Implementation Shortfall) 사전 추정 및 로깅

**적용 대상 파일/함수**: `src/brokers/router.py::OrderRouter.place_order()` 신규 `pre_flight_is_estimate()` 헬퍼 추가

**접목 방법**: 특허 (c)(d) 구성요소의 "IS 추정 후 라우팅 선택" 개념에서 착안하여, `OrderRouter.place_order()` 진입 시점에 `pre_flight_is_estimate(req, market_snapshot)` 함수를 호출한다. 이 함수는 단순 공식 `IS_est = spread/2 + market_impact_coeff * sqrt(qty / avg_daily_volume)`으로 IS를 추정하고 결과를 `observability` 메트릭으로 기록한다(`src/observability/` 연계). 실제 체결 후 `BrokerFill`에서 실현 IS를 계산해 사전 추정과 비교하여 `is_prediction_error` 메트릭을 산출한다. IS 추정치가 `algo_params["max_is_bps"]` 임계값 초과 시 주문 보류 후 전략 레이어에 콜백을 반환하는 선택적 게이트 기능도 추가 가능.

이 구현은 특허 (b) "실행 스타일 확률(Auto/RFQ/Voice)" 구성요소를 채택하지 않고, IS 추정 수식도 BlackRock 방식과 다른 단순 파라메트릭 모델을 사용하여 독립항 전체 구성요소 충족을 회피한다.

**기대 효과**: 주문별 거래비용 사전/사후 비교가 가능해져 전략 수익률에서 실행 비용 기여분을 정량화할 수 있다. `src/observability/` 메트릭과 연동하면 브로커·전략별 슬리피지 드리프트를 실시간 모니터링 가능 ([[10-broker-api-comparison]] §6 observability 연계).

**저비용 검증 경로**: `tests/test_is_estimator.py`에서 mock 시장 스냅샷 → IS 추정 → fill 비교 단위 테스트. 이후 백테스트 레포트에 `avg_is_bps` 컬럼 추가로 전략별 실행 비용 비교.

---

### 관련 연구 노트
- [[34-patents-execution-algos]]
- [[07-market-microstructure-basics]]
- [[10-broker-api-comparison]]

### 연결 이슈
- #84 특허 리서치
- #73 브로커 어댑터 async 마이그레이션 (실행 알고 선행)


## 작업 내역

### 2026-04-25

**현황**: 0/30+ AC 완료 (구현 대기)
**완료된 항목**: 없음
**미완료 항목**: 전 항목
**변경 파일**: 0개 (구현 시작 전)

**진행 사항**:
- `/ri /plan` 실행 → ralplan v3 컨센서스 워크플로우 통과
  - Planner v1 → Architect ITERATE (12개 P0/P1/P2 패치) → Planner v2 → Critic ITERATE (5개 critical/major + minor) → Planner v3 → Critic **APPROVE**
- 01_plan.md 의 `## 구현 계획` 섹션 작성 완료 (~580줄, Phase A-E 구체화)
- 프론트매터 `status: draft` → `status: planned` 갱신
- 의존성 #70/#69 모두 Merged 확인 (dummy sizer 불요)
- **Phase A (M1 게이트) 구현 + 검증 완료** — `/team 3` 으로 3 worker 병렬 진행
  - worker-1: `src/live/__init__.py`, `types.py` (OrderStatus / WALEvent / WALCorruption / Tick), `.ai.md` 신규
  - worker-2: `src/live/conversion.py` (`intent_to_order_request` + `SYMBOL_STEP_SIZES` 3종), `src/brokers/base.py::OrderAck.reject_reason` 필드 추가
  - worker-3: `src/live/wal.py` (WAL write/replay + WALWriteFailed), `src/live/process_lock.py` (filelock 기반), `pyproject.toml` 에 `filelock>=3.13` 추가
  - 테스트: 신규 31건 (test_live_types.py 6 + test_conversion.py 9 + test_order_ack_extension.py 3 + test_wal.py 9 + test_process_lock.py 4) **전수 통과**
  - 회귀: broker/adapter 365건 **전수 통과** (`OrderAck.reject_reason` 추가가 기존 호출자 영향 없음 확인)
- 다음 단계: Phase B (PaperBroker + MockMatchingEngine + 단위 테스트) — 사용자 확인 후 진행
- **#94 의존성 주입** — Phase C `loop.py` 가 `load_orchestrator_from_yaml(Path("configs/orchestrator/production.yaml"), ...)` 한 줄로 부트해야 함. `tests/live/test_daemon_boot.py` 로 `momo-btc-v2` + `momo-btc-v2-meta` 두 ID 등록 회귀 가드. 01_plan.md 4군데 (의존성 / Phase C-1 / Phase C AC / Guardrails Must Have+Must NOT) 에 못박음. #94 머지 후 rebase 시 활성화.

### 2026-04-26 (Phase B)

**현황**: Phase B 완료 — PaperBroker + MockMatchingEngine
**완료된 항목**:
- `src/execution/mock_matching.py` (Phase 1 정책: 즉시 100% 체결, 0-슬립, taker 0.05% / maker 0.02%)
- `src/execution/paper_broker.py` (AsyncBrokerAdapter Protocol 11개 메서드 모두 구현, WAL replay 복원, kill-switch 게이트, fills 큐)
- `src/execution/.ai.md` 갱신 (Phase B 산출물 반영)
**테스트**: 신규 23건 (test_mock_matching.py 11 + test_paper_broker.py 12) **전수 통과**
**회귀**: broker/adapter/execution **394/394 통과**
**다음 단계**: Phase C (live loop + reconnect + Binance public WS + executor seam 함수)

### 2026-04-26 (Phase C)

**현황**: Phase C 완료 — live loop + reconnect + feed + executor
**완료된 항목**:
- `src/live/feed.py` (MarketDataFeed Protocol + BinancePublicFeed — Binance USDT-M public aggTrade WS, API 키 불요)
- `src/live/reconnect.py` (backoff_delay 지수 + jitter + with_reconnect 래퍼, max_attempts)
- `src/live/executor.py` (`execute_intents` seam 함수 — Phase 2 전환 지점, broker 인자만 swap)
- `src/live/loop.py` (Shadow Live Loop, Windows SelectorEventLoop 정책, production.yaml fallback + warning 로그, ProcessLock, asyncio.Queue maxsize=1 latest-only)
- `src/live/.ai.md` 갱신 (Phase C-1 산출물 반영)
**테스트**: 신규 25건 (test_feed.py 5 + test_reconnect.py 6 + test_executor.py 7 + test_loop.py 7) **전수 통과**
**회귀**: live or loop or feed or executor or reconnect → **69/69 통과**, broker or adapter or execution → **402/402 통과**
**다음 단계**: Phase D (Kill-switch trigger 강화 + paper 메트릭 8종 + Time source 검증)

### 2026-04-26 (Phase D)

**현황**: Phase D 완료 — Kill-switch trigger 3종 강화 + paper 전용 메트릭 8종 + Time source 검증
**완료된 항목**:
- `src/ops/triggers.py` 강화 — `DrawdownTrigger` peak tracking 추가, `ApiErrorRateTrigger` 신규 (5분 sliding window, 임계 5%, min_samples 20), `FillAnomalyTrigger` JSONL 덤프 옵션 (`dump_path`)
- `src/observability/metrics.py` — paper 전용 메트릭 8종 신규 (paper_fills_total, paper_pnl_usdt, paper_position_qty, paper_equity_usdt, paper_order_ack_latency_ms, paper_drawdown_ratio, paper_fee_usdt_total, wal_write_error_total)
- `src/ops/.ai.md` 갱신 (4 trigger 클래스)
- `src/observability/.ai.md` 갱신 (총 23개 메트릭, paper USDT 단위 정책)
**테스트**: 신규 22건 (test_kill_switch.py +6 + test_paper_metrics.py 8 + test_paper_observability_integration.py 7) **전수 통과**
**회귀**: live or loop or feed or executor or reconnect or broker or adapter or execution or ops or metric or observability **440/440 통과**
**다음 단계**: Phase E (shadow_run.py + shadow_report.py + 롤백 injection 테스트)

### 2026-04-26 (Phase E)

**현황**: Phase E 완료 — Shadow 운영 도구 + 리포트 + 롤백 injection 테스트
**완료된 항목**:
- `scripts/shadow_run.py` (CLI 진입점, `--symbols`/`--duration`/`--max-iterations`/`--production-yaml` 등)
- `scripts/shadow_report.py` (WAL 파싱 → daily PnL → daily return → Sharpe 비교 4조건 + Strategy returns export + Exit Criteria 자동 검증)
- `scripts/.ai.md` 갱신 (Shadow Live Loop 섹션)
- `tests/test_rollback_injection.py` (롤백 트리거 3종 + Exit Criteria 통합)
**테스트**: 신규 41건 (test_shadow_run_cli.py 7 + test_shadow_report.py 27 + test_rollback_injection.py 7) **전수 통과**
**회귀**: broker or adapter or execution or live or ops or metric or observability or shadow or rollback **456/456 통과**

**모든 Phase A-E 코드 작업 완료**. 남은 항목 (실 운영 의존):
- 10 거래일 shadow 운영 로그 + 주문 N≥30 기록 → PR 머지 후 #94 (production.yaml) 머지 + 실 Binance public WS 운영
- Exit Criteria 5종 문서화된 증거 → 실 운영 후 `02_implementation.md` 작성
- work folder → done 이동 → PR 머지 후

