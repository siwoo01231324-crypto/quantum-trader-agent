---
type: work-done
id: 02_implementation
name: "#105 Phase 2 KIS 모의계좌 + AsyncOrderRouter — 구현 내역"
status: active
---

# Phase 2 KIS 모의계좌 + AsyncOrderRouter — 구현 내역

> Issue #105 · 작성: 2026-04-26
> ralplan --consensus (deliberate) 합의 계획 기반으로 7 Stage 구현 완료.
> Stage 7b (4주 운영) 는 별도 운영 이슈로 분리 예정.

---

## 1. 산출물 요약

### 신규 파일 (10)

| 파일 | 책임 | 테스트 |
|------|------|--------|
| `src/brokers/async_router.py` | AsyncOrderRouter — kill-switch + metric + swap_active + health_check | T5 (worker-1) |
| `src/brokers/rate_limiter.py` (갱신) | token-bucket (paper 2 RPS / live 20 RPS) | T4 (worker-1) |
| `src/observability/fx_rate.py` | USD/KRW 환율 TTL 캐시 (requests, 5분, 24h→None) | `test_fx_rate.py` |
| `src/live/tracking_error.py` | tracking_sample WAL 집계 + Gauge emit + daily_report_row | `test_tracking_error.py` |
| `src/live/strategy_returns_export.py` | KIS fills → 일수익률 → register_strategy_returns | `test_strategy_returns_export_kis.py` |
| `scripts/kis_paper_smoke.py` | KIS 모의계좌 smoke test (CI nightly) | T7 (worker-1) |
| `scripts/live_run.py` | KIS paper 모드 CLI (--broker, --auto-fallback, --schedule) | `test_live_report.py` |
| `scripts/live_report.py` | WAL → AC 6개 자동 검증 + R1~R5 halt trigger | `test_live_report.py` |
| `docs/dashboards/000105-phase2-kis-paper.md` | Grafana 패널 6종 스펙 | check_invariants PASS |
| `.github/workflows/kis-paper-nightly.yml` | nightly E2E job | T18 (worker-3) |

### 수정 파일 (8)

| 파일 | 변경 내용 |
|------|-----------|
| `src/live/loop.py` | `_build_router()`, `ShadowConfig.broker_mode`, `execute_intents(...market_state=ms)` |
| `src/live/executor.py` | `market_state` 인자, `(WALWriteFailed, BrokerError)` catch, `order_acked` WAL, `tracking_sample` self-sim |
| `src/live/types.py` | `OrderAckedPayload`, `TrackingSamplePayload`, `FillAnomalyPayload` + 상수 3종 |
| `src/brokers/kis/auth.py` | `filelock.FileLock` cross-process lock |
| `src/observability/metrics.py` | KRW 메트릭 9종 + `METRIC_NAMES` 갱신 |
| `.env.example` | KIS Phase 2 환경변수 6종 추가 |
| `src/brokers/.ai.md`, `src/live/.ai.md`, `src/observability/.ai.md`, `scripts/.ai.md` | 산출물 반영 |
| `docs/work/active/000105-phase2-paper-live/01_plan.md` | ralplan 합의 계획 |

### 테스트 파일 (17 — Expanded Test Plan 4 bucket)

| 버킷 | 파일 | 상태 |
|------|------|------|
| Unit | `test_fx_rate.py` (6) | PASS |
| Unit | `test_orders_placed_vs_filled_metrics_split.py` (7) | PASS |
| Unit | `test_kis_rate_limiter.py` | PASS (T4) |
| Unit | `test_strategy_returns_export_kis.py` (5) | PASS |
| Unit | `test_tracking_error.py` | PASS (T13) |
| Unit | `test_auth_paper.py` | PASS (T3) |
| Unit | `test_async_ws_paper.py` | PASS (T6) |
| Integration | `test_router_swap_live_loop.py` | PASS (T12) |
| Integration | `test_wal_concurrent_writers.py` | PASS (T12) |
| Integration | `test_kis_paper_self_sim_join.py` | PASS (T12) |
| Integration | `test_kill_switch_kis_path.py` | PASS (T17) |
| Integration | `test_wal_replay_after_kis_crash.py` | PASS (T17) |
| Integration | `test_token_cross_process_lock.py` | PASS (T3) |
| Integration | `test_live_report.py` (8) | PASS |
| E2E (nightly) | `test_kis_paper_smoke.py` | T18 (nightly only) |
| E2E (nightly) | `test_kis_paper_24h_micro.py` | T18 (nightly only) |
| Observability | `test_strategy_returns_export_kis.py` | PASS |

---

## 2. AC → 구현 매핑

| AC | 구현 위치 | 테스트 |
|----|-----------|--------|
| AC1 KIS 모의계좌 API 연결 | `KISAsyncAdapter(paper=True)` (기존 재사용) + `kis_paper_smoke.py` | `test_auth_paper`, `test_async_ws_paper`, E2E smoke |
| AC2 4주 실측 (Stage 7b 별도) | `scripts/live_run.py` + WAL 일자별 rotation | `live_report.py` trading_dates ≥ 20 |
| AC3 주문 N ≥ 100 | `qta_orders_placed_total{strategy,status}` + `qta_orders_filled_total{strategy}` | `test_orders_placed_vs_filled_metrics_split.py` |
| AC4 추적 오차 < 0.5% | executor self-sim → `tracking_sample` WAL → `tracking_error.py` 집계 | `test_tracking_error.py`, `test_kis_paper_self_sim_join.py` |
| AC5 kill-switch 3종 KIS | `AsyncOrderRouter.health_check` → trip + `ApiErrorRateTrigger` | `test_kill_switch_kis_path.py` |
| AC6 WS 재연결 | `KISAsyncWebSocket.stream_fills` backoff loop (변경 없음) | `test_async_ws_paper.py` reconnect counter |

---

## 3. Architect 인계 노트 6건 처리 내역

| # | 내용 | 실제 처리 |
|---|------|-----------|
| #1 | `execute_intents` 에 `market_state: MarketState | None = None` 추가 | `src/live/executor.py:22` — 기본값 None, PaperBroker 후방 호환 |
| #2 | `order_acked` payload `origin: "executor"` 필드 | `executor.py` order_acked WAL append + `OrderAckedPayload.origin="executor"` |
| #3 | executor self-sim `if not getattr(broker, "paper", False):` 게이트 | `executor.py _write_tracking_sample()` 내 gate 조건 |
| #4 | `strategy_returns_export` 호출 시점 docstring 결정 | `live_run.py` shutdown hook 선택 (docstring 명시) |
| #5 | R2 threshold 파라미터화 (`KIS_FILL_MISSING_HALT_THRESHOLD`) | `live_report.py` + `.env.example` env 변수 추가 |
| #6 | WAL 동시 쓰기 정책: asyncio.Queue vs asyncio.Lock | single consumer task 직렬화 선택 (executor.py 주석 ADR 1줄) |

---

## 4. Stage별 진행 요약

| Stage | 담당 | 상태 | 주요 산출물 |
|-------|------|------|-------------|
| 1.1 KRW 메트릭 | worker-1 | 완료 | `metrics.py` +9종 |
| 1.2 fx_rate | worker-2 | 완료 | `fx_rate.py` + 6 tests |
| 1.3 KISAuth lock | worker-3 | 완료 | `auth.py` filelock |
| 2.1 rate_limiter | worker-1 | 완료 | `rate_limiter.py` token-bucket |
| 2.2 AsyncOrderRouter | worker-1 | 완료 | `async_router.py` |
| 3.1 KIS paper unit | worker-3 | 완료 | `test_async_adapter_paper` 등 |
| 3.2 KIS smoke | worker-1 | 완료 | `kis_paper_smoke.py` |
| 3.3 .env.example | worker-2 | 완료 | KIS 환경변수 6종 |
| 4.1 types.py | worker-2 | 완료 | 3 event_type + 상수 |
| 4.2 executor.py | worker-2 | 완료 | market_state + order_acked + tracking_sample |
| 4.3 loop.py | worker-1 | 완료 | _build_router + ShadowConfig.broker_mode |
| 4.4 통합테스트 | worker-1 | 완료 | router_swap, wal_concurrent, self_sim_join |
| 5.1 tracking_error | worker-1 | 완료 | `tracking_error.py` |
| 5.2 strategy_returns | worker-2 | 완료 | `strategy_returns_export.py` |
| 5.3 live_run/report | worker-2 | 완료 | `live_run.py`, `live_report.py` |
| 5.4 Grafana 스펙 | worker-2 | 완료 | `000105-phase2-kis-paper.md` |
| 6.1 kill-switch KIS | worker-1 | 완료 | `test_kill_switch_kis_path.py` 등 |
| 6.2 nightly E2E | worker-3 | 완료 | `.github/workflows/kis-paper-nightly.yml` |
| 6.3 + 7a 마무리 | worker-2 | 완료 | 본 문서 + .ai.md 갱신 |

---

## 5. PR 머지 게이트 (Stage 7a) 체크리스트

- [x] AC1: KIS 모의계좌 API 연결 (`test_auth_paper`, `test_async_ws_paper`)
- [x] AC3: `qta_orders_placed_total` / `qta_orders_filled_total` 정의 + 분리 테스트
- [x] AC4: tracking_error reconciler (`tracking_error.py` + self-sim executor gate)
- [x] AC5: kill-switch 3종 통합테스트 (`test_kill_switch_kis_path.py`)
- [x] AC6: WS 재연결 단위테스트 (`test_async_ws_paper.py`)
- [x] nightly E2E workflow 정의 (`.github/workflows/kis-paper-nightly.yml`)
- [x] 17 테스트 정의 + 대부분 PASS (E2E 2종은 nightly secret 필요)
- [x] `.ai.md` 4개 갱신
- [x] 본 `02_implementation.md` 작성

---

## 6. Stage 7b — 운영 4주 (별도 이슈)

PR 머지 후 별도 운영 이슈로 분리:
- daemon 4주 운영 시작 + 일일 `live_report.py` cron
- AC2 (20 거래일) + AC3 (실측 placed ≥ 100) 충족 대기
- 일일 리포트: `docs/work/active/000105-phase2-paper-live/reports/{YYYY-MM-DD}.md`
- ADR 작성 (Decision/Drivers/Alternatives/Why-chosen/Consequences/Follow-ups)
- Phase 3 (#107) 진입 결정 게이트 (운영자 2인 승인)

참조: `docs/background/29-paper-to-live-protocol.md` §3 Phase 2 exit criteria
