# [#105] Phase 2 — KIS 모의계좌 + AsyncOrderRouter — 작업 계획 (초안)

> 작성: 2026-04-26
> 본 문서는 `/start-issue` 가 생성한 **AC 체크리스트 초안** 이다.
> 구현 시작 전 `/plan 105` 으로 구체적 구현 계획을 작성해야 한다.

## 완료 기준 — Exit Criteria (Phase 2 승격)

- [ ] KIS 모의계좌 API 연결 정상 (인증/주문/체결/조회)
- [ ] **4주 (20 거래일) 실측 운영 로그**
- [ ] 주문 건수 N ≥ 100 기록
- [ ] 모의계좌 체결 vs 자체 체결 시뮬 추적 오차 < 0.5%
- [ ] kill-switch 자동 트리거 3종 KIS 환경에서 동작 검증
- [ ] WS 단절 자동 재연결 정상 (Phase 1 reconnect 로직 KIS 호환 확인)

## 의존성

- ✅ #80 머지 (Phase 1 Shadow Paper) — `src/live/`, `src/execution/paper_broker.py` 인프라 일체
- ✅ #73 머지 (브로커 어댑터 async 마이그레이션) — `src/brokers/kis/async_adapter.py`
- ✅ #94 머지 (메타라벨러 production 활성화) — `configs/orchestrator/production.yaml` `momo-btc-v2-meta` 등록 적용 가능

## 범위 (이슈 본문 요약)

### 신규 모듈
- `src/brokers/router.py::AsyncOrderRouter` — sync OrderRouter 의 async 확장
  - kill-switch 게이트 + 메트릭 수집 + 다중 broker 등록 + `swap_active` async 지원
  - PaperBroker / KIS AsyncAdapter 양쪽 등록 → 런타임 active swap
- `src/brokers/kis/async_paper_adapter.py` — KIS 모의계좌 전용 AsyncBrokerAdapter
  - 기존 `async_adapter.py` (실거래) 와 분리 또는 paper 모드 옵션
  - 모의계좌 인증, 주문, 체결 폴링, 포지션 조회

### `src/live/loop.py` 수정 (최소 침습)
- `execute_intents()` 의 `broker` 인자를 `AsyncOrderRouter` 로 교체
- WAL/observability/kill-switch 경로 동일 (#80 인프라 재사용)

### KRW 메트릭
- `qta_paper_pnl_krw` (Gauge, label: strategy) — 기존 `qta_paper_pnl_usdt` 와 병행

## 참고

- `docs/work/done/000080-paper-broker/01_plan.md` — Phase 1 ralplan v3 plan + 후속 이슈 분리 목록
- `docs/background/29-paper-to-live-protocol.md` §3.2 Phase 2 정의·exit criteria·롤백
- `docs/background/09-system-components.md` §3 FMEA, §5 MVP 수용
- `docs/background/10-broker-api-comparison.md` — KIS API 특성

## 롤백 트리거 ([[29-paper-to-live-protocol]] §3.4)

- KIS API 오류율 > 10% (Phase 1 의 5% 임계 보다 보수적)
- 체결 누락 > 1건
- Sharpe 괴리 (Phase 1 shadow vs Phase 2 paper) > 0.5

## 주의사항

- **LLM 이 라이브 결정에 직접 개입 금지** (CLAUDE.md 불변식 #6)
- **Phase 1 코드 경로 우회 금지** — `execute_intents()` seam 만 swap, WAL/kill-switch/메트릭 경로 동일
- **본 이슈는 KIS 모의계좌만** — 실자금 (Phase 3 Live Pilot, #107) 은 별도 이슈
- **KillSwitch `threading.Lock` → `asyncio.Lock` 전환은 본 이슈 범위 외** (#108 별도)

## Out of Scope

- Phase 3 Live Pilot (실자금 5%, 8주 실측) → #107
- Phase 4 Full Production
- 슬리피지 모델 활성화 (`SquareRootImpact`) → #109
- Partial fill 지원 → #110
- 특허 차용 4건 → #111-#114

## 구현 계획

> ralplan --consensus (deliberate) loop 2회 합의 완료 (2026-04-26 ~ 2026-04-27)
> Planner v2 + Architect SOUND + Critic APPROVE.
> Architect 인계 노트 6건은 해당 Stage 위치에 `> [Architect note]` blockquote 로 인라인 부착됨.

### RALPLAN-DR Summary

#### Principles (5)

1. **Single-seam swap (불변식)**: Phase 2 의 유일한 진입 변경은 `src/live/loop.py::run_shadow_loop` 이 `PaperBroker` 대신 `AsyncOrderRouter` 를 `execute_intents()` (executor.py:17-24) 의 `broker` 인자로 주입. `execute_intents` 외부 호출 계약 (반환 타입, 파라미터 의미) 은 불변. seam 외 변경이 발생하는 파일은 각각 정당화 (Stage 4).
2. **Sync→Async 인터페이스 평행이동**: 신규 `AsyncOrderRouter` 는 sync `OrderRouter` (router.py:15-113) 의 책임 집합 (kill-switch gate, metric emit, `swap_active`, `health_check`→trip) 을 async 로 재현. 새 책임 추가 금지.
3. **Paper 분기 = adapter 옵션 (신규 paper 모듈 금지)**: `KISAsyncAdapter(paper=True)` (async_adapter.py:59-89) + `KISAuth(paper=True)` (auth.py:42-47) + `KISAsyncWebSocket(paper=True)` (async_ws.py:62) 그대로 사용. 별도 `KISPaperAdapter` 클래스 신설 금지.
4. **WAL-first 보존 + single-writer 직렬화**: WAL append (wal.py:27-35) 는 단일 writer task 에서만 호출. executor 의 `order_acked`·`tracking_sample` 추가 append 도 동일 consumer task 내 직렬 (loop.py:167-186).
5. **Self-sim single-pass 로 AC4 측정**: executor 가 KIS `place_order` ack 후 동일 `(req, market_state)` 로 `MockMatchingEngine.match()` 1회 더 호출 → `tracking_sample` event 1줄 WAL append. Shadow PaperBroker fan-out 은 토톨로지·잔고 발산으로 기각.

#### Decision Drivers (Top 3)

1. 불변식 보존 vs 신규 추상화 비용 — Phase 1 의 8 모듈 미변경
2. AC4 측정 정확성 — 비교 대상이 KIS paper API 체결 가격 vs MockMatchingEngine 시뮬 가격
3. 운영 위험 감소 — 코드 ~50 라인 (executor self-sim) vs 13+ 파일 (fan-out infra)

#### Viable Options + 결정

| # | 선택지 | 결정 | 근거 |
|---|--------|------|------|
| A | Shadow PaperBroker fan-out (v1) | 기각 | MockMatchingEngine 결정성 → 토톨로지, 잔고 발산 누적, asyncio.gather 동시성 |
| **A'** | **Self-sim single-pass** | **채택** | seam 보존 + AC4 정확 측정 + Phase 1 회귀 0 + 최소 코드 |
| B | KISPaperAdapter 별도 클래스 | 기각 | async_adapter.py 와 90% 중복 |
| C | sync OrderRouter + asyncio.to_thread | 기각 | event loop 충돌, 시그니처 불일치 |
| D | execute_intents list[broker] | 기각 | 불변식 위반 |

#### Pre-mortem (3 시나리오)

1. **KIS 토큰 cross-process 경합 + WAL 폭주 (T+5일)** — 완화: KISAuth `filelock.FileLock` (`.omc/state/kis_token_paper.lock`), ProcessLock (loop.py:132), `qta_kis_token_ttl_seconds`, WAL 디스크 알람.
2. **WS 체결통보 누락으로 tracking_sample join 실패 (T+8일)** — 완화: `order_acked` event 의 `broker_order_id` 로 join, `qta_kis_fill_missing_total` 별도 카운트, 결측률 >5% 시 daemon halt.
3. **멀티 strategy 동시 발주 + KIS 2 RPS 위반 (T+12일)** — 완화: `AsyncOrderRouter` token-bucket (paper=2 RPS, live=20 RPS), executor.py:73 catch 를 `(WALWriteFailed, BrokerError)` 로 확장, `qta_broker_rate_limit_hit_total`.

#### Expanded Test Plan (4 buckets, 17 파일)

- **Unit (7)**: `test_async_router`, `test_async_adapter_paper`, `test_auth_paper`, `test_async_ws_paper`, `test_kis_rate_limiter`, `test_kis_metrics_register`, `test_token_cross_process_lock`
- **Integration (6)**: `test_router_swap_live_loop`, `test_kis_paper_e2e_mock`, `test_kill_switch_kis_path`, `test_wal_replay_after_kis_crash`, `test_kis_paper_self_sim_join`, `test_wal_concurrent_writers`
- **E2E (2, nightly only)**: `test_kis_paper_smoke`, `test_kis_paper_24h_micro`
- **Observability (2)**: `test_orders_placed_vs_filled_metrics_split`, `test_strategy_returns_export_kis`

---

### 1. AC → 구현 단계 매핑

| AC | 구현 모듈 | 테스트 | 운영 모니터링 |
|----|-----------|--------|---------------|
| AC1 KIS 모의계좌 API 연결 | 기존 `KISAsyncAdapter(paper=True)` 재사용 + `scripts/kis_paper_smoke.py` | `test_async_adapter_paper`, `test_auth_paper`, `test_async_ws_paper`, `test_token_cross_process_lock`, e2e `test_kis_paper_smoke` | nightly smoke green |
| AC2 4주 실측 (Stage 7b 별도 운영 issue) | `scripts/live_run.py`, WAL 일자별 rotation | — | `live_report.py` distinct trading dates ≥ 20 |
| AC3 주문 N ≥ 100 | `qta_orders_placed_total{strategy,status}` (NEW) + `qta_orders_filled_total{strategy}` (FILLED) 분리 | `test_orders_placed_vs_filled_metrics_split` | Exit gate: `placed ≥ 100 AND filled ≥ placed * 0.95` |
| AC4 추적 오차 < 0.5% | executor self-sim → `tracking_sample` event, `src/live/tracking_error.py` 집계 | `test_kis_paper_self_sim_join`, `test_wal_concurrent_writers` | `qta_paper_kis_tracking_error` Gauge daily, 산식 `mean(\|kis_fill_price - sim_fill_price\| / sim_fill_price) < 0.005` |
| AC5 kill-switch 3종 KIS 동작 | 기존 `src/ops/triggers/*` 재사용 + `AsyncOrderRouter.health_check` → trip 연결 | `test_kill_switch_kis_path` | `qta_kill_switch_state{reason}` |
| AC6 WS 재연결 KIS 호환 | `KISAsyncWebSocket.stream_fills` (async_ws.py:206-238) 기존 backoff loop, 변경 없음 | `test_async_ws_paper` reconnect counter | `qta_broker_ws_reconnect_total{broker="kis"}` ≥ 1 |

### 2. 단계별 실행 순서

```
Stage 1 — 메트릭 + 환율 + cross-process lock (의존성 0)
 ├─ 1.1 KRW 메트릭 9종 등록 (metrics.py): qta_paper_pnl_krw, qta_paper_equity_krw,
 │       qta_kis_token_ttl_seconds, qta_kis_partial_fill_total,
 │       qta_paper_kis_tracking_error, qta_broker_rate_limit_hit_total,
 │       qta_kis_fill_missing_total, qta_orders_placed_total, qta_orders_filled_total
 ├─ 1.2 USD/KRW fetcher + 캐시 (src/observability/fx_rate.py)
 └─ 1.3 KISAuth cross-process file lock (auth.py 수정 — filelock 패턴)
       lock 파일: .omc/state/kis_token_paper.lock / kis_token_live.lock

Stage 2 — AsyncOrderRouter (의존성: Stage 1)
 ├─ 2.1 Red 단위테스트 (test_async_router.py — 7 cases)
 ├─ 2.2 src/brokers/async_router.py 구현
 └─ 2.3 src/brokers/rate_limiter.py — token-bucket (paper 2 RPS / live 20 RPS)

Stage 3 — KIS paper 통합 (의존성: Stage 2)
 ├─ 3.1 Red 통합테스트 (respx + fake ws)
 ├─ 3.2 scripts/kis_paper_smoke.py
 ├─ 3.3 .env.example 갱신
 └─ 3.4 test_token_cross_process_lock (multiprocessing fixture)

Stage 4 — Live loop seam swap + executor 확장 (의존성: Stage 2,3)
 ├─ 4.1 src/live/loop.py: _build_router() + ShadowConfig.broker_mode
 ├─ 4.2 src/live/executor.py 확장 (3 changes):
 │     (a) L73 catch (WALWriteFailed, BrokerError) — REJECTED ack down-grade
 │     (b) KIS ack 후 tracking_sample self-sim (engine.match + WAL append)
 │     (c) order_acked event WAL append (broker_order_id join key)
 ├─ 4.3 src/live/types.py: event_type 3종 (order_acked, tracking_sample, fill_anomaly)
 └─ 4.4 test_wal_concurrent_writers — single-writer 직렬성 검증

> **[Architect note #1]** `execute_intents` 시그니처에 `market_state: MarketState | None = None` 추가. `loop.py:179` consumer 가 `_tick_to_market_state(tick)` 전달. None 시 self-sim skip (PaperBroker 단독 호환). 변경 정당화 표 6건으로 확장.
> **[Architect note #2]** `order_acked` payload 에 `origin: "executor"` 필드 추가하여 PaperBroker 의 `order_submitted` 과 출처 분리.
> **[Architect note #6]** WAL 동시 쓰기 정책: WS fill listener 는 asyncio.Queue 경유로 consumer task 에서 단일 writer 보장 (선호) **또는** `WAL.write` 에 `asyncio.Lock` 추가. 구현 시 택 1 명시 + ADR 1줄.

** Stage 4 변경 5+1=6군데 정당화 (single-seam 원칙 미위반)**
- loop.py (4.1): seam swap 자체 — 원칙 정의
- executor.py (4.2a): BrokerError catch — 시그니처 불변, 내부 예외 처리만 확장
- executor.py (4.2b): self-sim tracking — for-loop 내부 직렬 WAL append
- executor.py (4.2c): order_acked WAL — 기존 _reject 옆 ack 기록 추가
- executor.py: market_state 인자 추가 (Architect note #1) — 기본값 None 으로 후방 호환
- types.py (4.3): 데이터 정의 — 실행 경로 불변

Stage 5 — Tracking error 집계 + 전략 수익률 export (의존성: Stage 4)
 ├─ 5.1 src/live/tracking_error.py — tracking_sample 집계 + Gauge emit
 │     산식: mean(|kis_fill_price - sim_fill_price| / sim_fill_price) < 0.005
 │     결측 fill (qta_kis_fill_missing_total) 은 계산 제외
 ├─ 5.2 src/live/strategy_returns_export.py — KIS WS fills + balance →
 │     전략별 일수익률 series → orchestrator.register_strategy_returns(strategy_id, series)
 │     (CLAUDE.md "register_strategy_returns 필수" 의무 — portfolio CVaR/ENB 침묵 방지)
 ├─ 5.3 scripts/live_report.py — AC 6개 자동 검증
 └─ 5.4 docs/dashboards/000105-phase2-kis-paper.md (Grafana 패널 6종)

> **[Architect note #3]** executor self-sim 호출에 `if not getattr(broker, "paper", False):` 게이트 추가. PaperBroker fallback (auto-fallback R1/R3 후) 시 sim-vs-sim 토톨로지 방지.
> **[Architect note #4]** `strategy_returns_export` 호출 시점은 구현 시 결정 (shutdown hook / 일 1회 cron / loop periodic 중 택 1). Stage 7a 머지 게이트에서 확인.

Stage 6 — Kill-switch KIS + nightly CI + auto-fallback (의존성: Stage 4)
 ├─ 6.1 ApiErrorRateTrigger ← KIS HTTP 5xx + 4xx 카운트 hook
 ├─ 6.2 .github/workflows/kis-paper-nightly.yml (E2E job)
 └─ 6.3 scripts/live_run.py: --auto-fallback (default true)
       trip 시 PaperBroker 단독 모드로 graceful 재시작

> **[Architect note #5]** R2 threshold (체결 누락 1건) 은 Phase 2 한정 보수적. 파라미터화 권고 (config 또는 env, 예: `KIS_FILL_MISSING_HALT_THRESHOLD=1`). Phase 3 진입 시 재검토.

Stage 7a — PR 머지 게이트 (의존성: Stage 1~6)
 ├─ 7a.1 PR 머지 조건: AC1 + AC3(메트릭 정의) + AC4(reconciler 동작)
 │       + AC5(kill-switch 통합테스트) + AC6(WS 재연결 단위테스트) + nightly E2E green
 │       + 17 테스트 전체 green
 ├─ 7a.2 .ai.md 갱신 (src/brokers/, src/live/, src/observability/, scripts/)
 └─ 7a.3 본 01_plan.md 완성 + 02_implementation.md 작성

Stage 7b — 운영 4주 (PR 머지 후, 별도 운영 issue)
 ├─ 7b.1 daemon 4주 운영 시작 + 일일 live_report.py cron
 ├─ 7b.2 AC2 (20 거래일) + AC3 (실측 N≥100) 충족 대기
 ├─ 7b.3 docs/work/active/000105-phase2-paper-live/02_operation.md 일일 리포트
 ├─ 7b.4 ADR 작성 (Decision/Drivers/Alternatives/Why-chosen/Consequences/Follow-ups)
 └─ 7b.5 Phase 3 (#107) 진입 결정 게이트 (운영자 2인 승인)
```

### 3. 변경/생성 파일 목록

**신규 (10)**

| 파일 | 책임 |
|------|------|
| `src/brokers/async_router.py` | AsyncOrderRouter — sync OrderRouter async 평행이동 |
| `src/brokers/rate_limiter.py` | token-bucket (paper 2 RPS / live 20 RPS) |
| `src/observability/fx_rate.py` | USD/KRW 환율 fetch + TTL 캐시 |
| `src/live/tracking_error.py` | tracking_sample 집계 + Gauge emit + 일일 리포트 row |
| `src/live/strategy_returns_export.py` | KIS fills → 전략별 일수익률 → register_strategy_returns |
| `scripts/kis_paper_smoke.py` | KIS 모의계좌 스모크 (CI nightly + 수동) |
| `scripts/live_run.py` | KIS paper 모드 진입점 (CLI --broker / --auto-fallback / --schedule) |
| `scripts/live_report.py` | WAL → AC 6개 자동 검증 리포트 |
| `docs/dashboards/000105-phase2-kis-paper.md` | Grafana 패널 6종 |
| `.github/workflows/kis-paper-nightly.yml` | nightly E2E job |

**수정 (8)** — 각각 `execute_intents` 호출 계약 불변

| 파일 | 변경 내용 |
|------|-----------|
| `src/live/loop.py` | `_build_router()`, `ShadowConfig.broker_mode`, `execute_intents(...broker=router, market_state=ms)` |
| `src/live/executor.py` | `(WALWriteFailed, BrokerError)` catch, `market_state` 인자, `order_acked` WAL, `tracking_sample` self-sim |
| `src/live/types.py` | event_type 3종 (`order_acked`, `tracking_sample`, `fill_anomaly`) |
| `src/brokers/kis/auth.py` | `filelock.FileLock` 적용 |
| `src/observability/metrics.py` | KRW 메트릭 9종 + `METRIC_NAMES` 갱신 |
| `.env.example` | KIS 환경변수 추가 |
| `src/brokers/.ai.md`, `src/live/.ai.md`, `src/observability/.ai.md`, `scripts/.ai.md` | 산출물 반영 |
| `docs/work/active/000105-phase2-paper-live/01_plan.md` | 본 문서 |

**테스트 (17)** — 위 Expanded Test Plan 4 bucket

### 4. 테스트 전략

- **TDD**: Stage 2~5 모두 Red→Green→Refactor. 각 Stage 시작 시 해당 테스트 파일 Red 먼저.
- **CI**: `pytest -m "not e2e_kis_paper"` (일반 PR), `pytest -m e2e_kis_paper` (nightly only, GitHub secret).
- **WAL concurrent writers**: Windows runner 포함 (fsync atomicity).

### 5. Guardrails

**Must Have**
- TDD Red→Green→Refactor
- AsyncOrderRouter 단위테스트 (kill-switch gate, metric emit, swap_active env-flag, rate-limit hit)
- KIS paper adapter 단위테스트 (mock HTTP/WS, 인증, 주문, 체결 폴링, 포지션 조회)
- `execute_intents()` seam swap 통합테스트
- kill-switch 3 trigger × KIS 통합테스트
- WAL append/replay 회귀 + single-writer 직렬성 검증
- 모든 신규 메트릭 `METRIC_NAMES` 등록 + 검증
- `order_acked` event 로 `broker_order_id` join key 보장
- KISAuth cross-process file lock + multiprocessing 테스트
- `register_strategy_returns` 호출 경로 (CLAUDE.md 불변식)
- `qta_orders_placed_total` / `qta_orders_filled_total` 분리

**Must NOT Have**
- LLM 의 라이브 결정 직접 개입 (CLAUDE.md 불변식 #6)
- `execute_intents()` 외부 호출 계약 변경 (반환 타입 / 파라미터 의미 — 단 `market_state` Optional 인자 추가는 후방 호환)
- Shadow PaperBroker fan-out (기각, self-sim single-pass 채택)
- `KillSwitch threading.Lock → asyncio.Lock` 전환 (#108)
- 슬리피지 모델 활성화 (#109)
- Partial fill 지원 (#110, `fill_anomaly` event 까지만)
- 실자금 경로 (Phase 3, #107)
- 별도 KIS paper 전용 모듈 신설
- KIS WS background keepalive task

### 6. 위험·엣지케이스

1. **KIS API rate limit** — paper 2 RPS / live 20 RPS, 토큰 1분 1회. token-bucket + REJECTED ack down-grade.
2. **NEW vs FILLED 분리** — `qta_orders_placed_total` (즉시) / `qta_orders_filled_total` (WS fill 후). AC3 exit gate: `placed≥100 AND filled≥placed*0.95`.
3. **broker_order_id 매핑** — KIS WS `client_order_id=""` (async_ws.py:179). `order_acked` event 로 매핑 → WS fill `broker_order_id` (async_ws.py:181) 로 join. 실패 시 `qta_kis_fill_missing_total`.
4. **cross-process token 경합** — `filelock.FileLock` (`.omc/state/kis_token_paper.lock`). 획득 timeout 0 → cache fallback.
5. **USD/KRW 환율 캐시** — TTL 5분, fetch 실패 시 stale + warning, 24h 미갱신 시 KRW 메트릭만 송출 중단 (kill-switch trip 안 함 — 보조 메트릭).
6. **WS 재연결** — KIS WS base 1s, max 10s, jitter ±0.2 (async_ws.py:36-38). 각 broker 별 reconnect counter 분리.
7. **멀티 strategy 동시 발주** — executor for-loop 직렬 await, router 무잠금 (single asyncio task 보장).
8. **register_strategy_returns 누락 시 침묵** — Stage 5.2 `strategy_returns_export.py` 가 KIS fills + balance → daily series → register 호출. 누락 시 portfolio CVaR/ENB 침묵 (CLAUDE.md 불변식 위반).
9. **모의계좌 영업시간 외** — KIS 거부 코드를 REJECTED ack 로. 시장 캘린더 통합은 #111 후속.
10. **토큰 만료/갱신 + 캐시 보안** — `_should_renew` 5분 전, 캐시 파일 0600 강제, cross-process lock 으로 중복 발급 차단.

### 7. 운영 모니터링 단계 — 자동 롤백 트리거 5종 + auto-fallback

| # | 트리거 조건 | 윈도우 | 자동 액션 | 메트릭 |
|---|------------|--------|-----------|--------|
| R1 | KIS API 5xx error rate > 10% | 15분 | daemon halt + alert + PaperBroker 폴백 | `qta_broker_request_latency_seconds{broker="kis"}` 5xx |
| R2 | 체결 누락 ≥ 1건 *(Architect note #5: 파라미터화)* | 1시간 | daemon halt + 사후 조사 (자동 폴백 안 함) | `qta_kis_fill_missing_total` |
| R3 | Tracking error > 0.5% 5분 연속 | 5분 rolling | daemon halt + alert + PaperBroker 폴백 | `qta_paper_kis_tracking_error` |
| R4 | 토큰 재발급 실패 연속 3회 | — | daemon halt + alert | `qta_broker_keepalive_failure_total{broker="kis"}` |
| R5 | 모의 잔고 불일치 > 1% | 일일 | daemon halt + alert | `live_report.py` 일일 diff |

**자동 폴백**: R1/R3 trip 시 `AsyncOrderRouter.swap_active(paper_broker)` → graceful 재시작, WAL 에 `mode_switched` event. R2/R4/R5 는 daemon halt only (운영자 수동 재시작).

**Grafana 6 패널**: KRW PnL/equity, 5xx rate (R1), tracking error (R3), WS reconnect, rate-limit hit, 토큰 TTL.

**Exit 게이트 분리**:
- **PR 머지 게이트** (Stage 7a): AC1 + AC3(메트릭 정의) + AC4(reconciler) + AC5(통합테스트) + AC6(단위테스트) + nightly E2E green + 17 테스트 green
- **운영 완료 게이트** (Stage 7b, 별도 issue): AC2 (20 거래일) + AC3 (placed≥100 실측) + AC4 (p95 < 0.5% 실측). 운영자 2인 승인 + ADR + Phase 3 (#107) 진입 결정.

---

## 다음 단계

1. Stage 1 시작 — 메트릭 9종 + `fx_rate.py` + KISAuth cross-process lock
2. Stage 2 — `AsyncOrderRouter` Red 테스트 (TDD)
3. 각 Stage 시작 시 해당 Architect note (#1~#6) 우선 반영
4. Stage 7a 머지 게이트 통과 후 별도 운영 이슈로 Stage 7b 분리
