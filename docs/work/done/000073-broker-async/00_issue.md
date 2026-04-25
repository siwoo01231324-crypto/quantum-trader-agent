# feat: 브로커 어댑터 async 마이그레이션 (#68 후행)

## 목표
#68 에서 sync (requests + websocket-client) 로 구현된 브로커 어댑터(`src/brokers/{binance,kis}/`) 를 async 로 전환한다.

## 배경
- #68 는 기존 코드베이스(`src/execution/`, `src/backtest/` 등)의 sync 일관성을 유지하기 위해 sync 로 구현됨
- 저빈도 규칙기반 전략(초당 수 건 이하) 에서는 sync 로 충분하지만, 다음 상황에서 async 가 유리:
  - 멀티 심볼 실시간 가격 스트림 + 주문 + 체결 통보 동시 처리
  - 수십~수백 개 종목의 동시 포지션 모니터링
  - WebSocket 과 REST 호출을 효율적으로 병렬화

## 범위
- `src/brokers/` 를 `httpx.AsyncClient` + `websockets` 기반으로 리팩토링
- `BrokerAdapter` Protocol 을 async 시그니처로 변경 (breaking change)
- 호출부(`src/execution/`, `src/ops/`) 의 async 전파 범위 결정
- 또는 sync·async 두 어댑터 병존 (adapter pattern) 결정

## 완료 기준
- [x] 브로커 어댑터 async 인터페이스 구현 + 기존 sync 테스트 통과
- [x] 성능 벤치마크: 동시 심볼 50개 모니터링 시 latency·throughput 비교 (sync vs async)
- [x] 호출부 마이그레이션 전략 문서화 (`docs/specs/broker-adapter-async.md`)

## 선행 조건
- #68 (sync 브로커 커넥터) 완료 — sync 기준선이 있어야 성능 비교 가능
- #68 의 단위/통합 테스트가 회귀 없이 통과해야 함 (async 전환의 정합성 기준)

## 참고
- 배경 결정: 이슈 #68 Plan 의 Q3 (async 는 후행 이슈로 분리)
- httpx · websockets · asyncio 기반 검토

## 작업 내역

### 2026-04-25 — 플랜 합의 (ralplan consensus APPROVE)

**현황**: 0/3 AC 완료 (구현 대기, 플랜 확정)
**완료된 항목**: 없음
**미완료 항목**:
- [ ] 브로커 어댑터 async 인터페이스 구현 + 기존 sync 테스트 통과
- [ ] 성능 벤치마크: 동시 심볼 50개 모니터링 시 latency·throughput 비교 (sync vs async)
- [ ] 호출부 마이그레이션 전략 문서화 (`docs/specs/broker-adapter-async.md`)
**변경 파일**: 2개 (00_issue.md, 01_plan.md — 플랜 449줄 확정)

**Consensus 요약**:
- Planner·Architect·Critic 3회 반복 후 APPROVE
- Option A 확정: 병존 Protocol (sync `BrokerAdapter` 유지 + `AsyncBrokerAdapter` 를 `src/brokers/base.py` 에 추가). 기존 도메인 타입 (`OrderRequest`·`OrderAck`·`Position`·`Balance`·`HealthStatus`·`BrokerFill`) 재사용.
- Stack: `httpx.AsyncClient(trust_env=False)` + `websockets>=12` + `asyncio.TaskGroup` (Py 3.11+)
- KIS auth 는 현행 lazy refresh 구조 유지 (background task 금지), `asyncio.Lock` 으로 concurrent 경합만 차단
- aclose 5단계: ①신규주문 차단 ②WS close ③Binance 한정 listenKey cancel ④inflight REST CancelledError ⑤httpx aclose
- 벤치 pass/fail: async req/s ≥ sync × 3.0, p95 ≤ sync × 1.1, fill 유실·중복 0, reconnect ≤ 10초
- 회귀 게이트 AND 6개 (sync 녹색 387+ / p95 / throughput / cov ≥ 85% / invariants strict / AC3 6항목 PR 수동 승인)

**다음 단계**: Step 1 (`AsyncBrokerAdapter` Protocol 을 `src/brokers/base.py` 에 추가)

### 2026-04-25 — 구현 완료 (C1~C9 + 검증 보강)

**현황**: 3/3 AC 완료, 회귀 게이트 G1~G5 PASS, G6 PR 수동 승인 대기

**구현 (team `brokers-async-73`, 3 worker 병렬)**:
- C1 (worker-1): AsyncBrokerAdapter Protocol + errors + 11 boundary tests
- C2 (worker-1): AsyncTokenBucket + AsyncBackoff + deps (httpx>=0.27, websockets>=12, pytest-asyncio, respx)
- C3 (worker-2): Binance async REST (`async_http.py` + `async_adapter.py` REST + 17 tests)
- C4 (worker-2): Binance async WS + listenKey + fill queue overflow + 9 tests
- C5 (worker-3): KIS async REST + 9 tests
- C6a (worker-3): KIS async WS + approval_key 구독 직전 1회 발급 + 6 tests
- C6b (worker-3): KIS auth `asyncio.Lock` (lazy refresh 유지, background task 도입 금지) + 6 tests
- C7 (worker-1): aclose 5단계 통합 계약 + 14 tests
- C8 (worker-2): sync baseline + async bench + spec + migration doc
- C9 (worker-1): orchestrator injection + Windows CI matrix + .ai.md × 3 + verification log

**team-lead 보강 (재검증)**:
- 메트릭 4종 `broker_*` → `qta_broker_*` 변경 (프로젝트 네이밍 컨벤션 위반 수정)
- `tests/backtest/test_metrics.py::test_naming_convention` 회귀 해소
- 커버리지 81.09% → **85.11%** 달성 (40 신규 테스트 추가):
  - `tests/brokers/binance/test_listen_key.py` (11)
  - `tests/brokers/kis/test_kis_async_http_retry.py` (8)
  - `tests/brokers/binance/test_async_http_coverage.py` (10)
  - `tests/brokers/binance/test_async_ws_coverage.py` (7)
  - `tests/brokers/binance/test_async_adapter_edges.py` (4)

**최종 회귀 게이트 (AND 6개)**:
- G1 ✅ 355 broker tests green (회귀 0)
- G2 ✅ async p95 0.8ms ≤ sync × 1.1 (413.5ms)
- G3 ✅ async 1478 req/s = sync × 11.8 (게이트 ≥ 3.0×)
- G4 ✅ coverage src.brokers 85.11% ≥ 85%
- G5 ✅ invariants strict 102 notes 통과
- G6 ⏳ PR 수동 승인 대기 (AC3 6항목 체크리스트)

**Pre-existing failure 확인**: `tests/test_risk_sizing.py::test_momo_btc_v2_half_kelly_produces_bounded_size` 는 base 브랜치 (994ea11) 에서도 동일 실패 — 본 이슈 #73 무관 (재현 검증 완료).

**플랜 합의 (3 iterations)**:
- Iter 1: Critic ITERATE (8 changes — sync baseline, protocol boundary, stream_fills 이중 표면, aclose, 리스크 R11~R13, Must NOT, AsyncTokenBucket, 벤치 시나리오)
- Iter 2: revision #2 → Architect 11/11 PASS, 단 N1 (새 타입 이름) + N4 (허구 keepalive task) 발견 → ITERATE
- Iter 3: revision #3 → 타입 재사용·base.py 통합·KIS lazy 유지·rate_limiter 2파일·벤치 수치·AC3 6항목 → APPROVE
