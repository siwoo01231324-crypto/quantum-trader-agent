# feat: 라이브 실행 프레임워크 Phase 2 — KIS 모의계좌 + AsyncOrderRouter (#80 후속)

## 배경

#80 (Phase 1 Shadow Paper) 머지 후 다음 단계. [[29-paper-to-live-protocol]] §3.2 의 4단계 프레임워크 중 **Phase 2 Live Paper** 를 구현한다.

#80 가 만든 인프라 (`src/live/loop.py`, `src/live/executor.py`, WAL, kill-switch trigger 3종, paper 메트릭 8종) 를 그대로 활용하고, **`execute_intents()` seam 함수의 `broker` 인자만** PaperBroker → KIS AsyncBrokerAdapter (모의계좌) 로 교체. 동일 코드 경로로 실거래 API 검증 가능.

## 의존성

- ✅ #80 머지 필수 (`src/live/`, `src/execution/paper_broker.py` 등 전체)
- ✅ #73 머지 (브로커 어댑터 async 마이그레이션) — 이미 머지됨
- 📅 #94 머지 권장 — `configs/orchestrator/production.yaml` 활성화로 `momo-btc-v2-meta` 등록

## 범위

### 신규 모듈
- `src/brokers/router.py::AsyncOrderRouter` (sync OrderRouter 의 async 확장)
  - kill-switch 게이트 + 메트릭 수집 + 다중 broker 등록 + `swap_active` async 지원
  - PaperBroker 와 KIS AsyncAdapter 둘 다 등록 가능, `swap_active` 로 런타임 교체
- `src/brokers/kis/async_paper_adapter.py` (KIS 모의계좌 전용 AsyncBrokerAdapter)
  - 기존 `src/brokers/kis/async_adapter.py` (실거래) 와 분리 또는 paper 모드 옵션
  - 모의계좌 인증, 주문, 체결 폴링, 포지션 조회

### `src/live/loop.py` 수정 (최소 침습)
- `execute_intents()` 의 `broker` 인자를 `AsyncOrderRouter` 로 교체
- Router 가 active broker 를 KIS paper adapter 로 라우팅
- WAL/observability/kill-switch 경로 동일 (#80 인프라 재사용)

### KRW 메트릭 추가
- `qta_paper_pnl_krw` (Gauge, label: strategy) — KIS 환경 USD/KRW 환산
- 기존 `qta_paper_pnl_usdt` 와 병행 (Binance Futures vs KIS 자산군 분리)

## Exit Criteria (Phase 2 승격 — [[29-paper-to-live-protocol]] §3.3)

> PR 머지 게이트(Stage 7a): AC1·AC5·AC6 + nightly E2E 인프라 구축 완료.
> 4주 실측 게이트(Stage 7b, 별도 issue #133): AC2·AC3·AC4 실측.

- [x] KIS 모의계좌 API 연결 정상 (인증/주문/체결/조회) — 인증/잔고 실증 (KRW 1천만원 정확 출력), 주문은 nightly cron(KST 월~금 10:00)으로 자동 검증
- [ ] **4주 (20 거래일) 실측 운영 로그** — #133 운영 이슈로 분리
- [ ] 주문 건수 N ≥ 100 기록 — #133 운영 이슈
- [ ] 모의계좌 체결 vs 자체 체결 시뮬 추적 오차 < 0.5% — #133 운영 이슈 (reconciler 코드는 완료, 실측 데이터 필요)
- [x] kill-switch 자동 트리거 3종 KIS 환경에서 동작 검증 — `tests/integration/test_kill_switch_kis_path.py` 통합테스트 4건 green
- [x] WS 단절 자동 재연결 정상 (Phase 1 reconnect 로직 KIS 호환 확인) — `tests/brokers/kis/test_async_ws_paper.py` 7건 green

## 롤백 트리거 (§3.4)

- KIS API 오류율 > 10% (Phase 1 의 5% 임계 보다 보수적)
- 체결 누락 > 1건
- Sharpe 괴리 (Phase 1 shadow vs Phase 2 paper) > 0.5

## 주의사항

- **LLM 이 라이브 결정에 직접 개입 금지** (CLAUDE.md 불변식 #6)
- **Phase 1 코드 경로 우회 금지** — `execute_intents()` seam 만 swap, WAL/kill-switch/메트릭 경로 동일
- **본 이슈는 KIS 모의계좌만** — 실자금 (Phase 3 Live Pilot) 은 별도 이슈
- KillSwitch `threading.Lock` → `asyncio.Lock` 전환 (Phase 3+ 멀티스레드 검토용) 은 본 이슈 범위 외

## 후속 (out of scope)

- **Phase 3 Live Pilot** (실자금 5%, 8주 실측 + 승인 2인)
- **Phase 4 Full Production** (M1~M5 스케일업)
- **슬리피지 모델 활성화** (`SquareRootImpact`) — `MockMatchingEngine` Phase 2+ 확장
- **Partial fill 지원** — `partial_fill_enabled=True`
- **특허 차용 4건** (VWAP volume blend, OrderRouter cost routing, TWAP volatility regime, IS pre-flight + TCA)

## 참고

- `docs/work/active/000080-paper-broker/01_plan.md` — Phase 1 ralplan v3 plan + 후속 이슈 분리 목록
- [[29-paper-to-live-protocol]] §3.2 Phase 2 정의·exit criteria·롤백
- [[09-system-components]] §3 FMEA, §5 MVP 수용
- [[10-broker-api-comparison]] — KIS API 특성

## 연결 이슈

- 선행: #80 (Phase 1 Shadow Paper) — **머지 필수**
- 선행 권장: #94 (메타 라벨러 production 활성화)
- 후속: Phase 3 Live Pilot, Phase 4 Full Production, 특허 차용 4건

## 작업 내역

### 2026-04-26

**현황**: 0/6 완료
**완료된 항목**:
- (없음)
**미완료 항목**:
- KIS 모의계좌 API 연결 정상 (인증/주문/체결/조회)
- 4주 (20 거래일) 실측 운영 로그
- 주문 건수 N ≥ 100 기록
- 모의계좌 체결 vs 자체 체결 시뮬 추적 오차 < 0.5%
- kill-switch 자동 트리거 3종 KIS 환경에서 동작 검증
- WS 단절 자동 재연결 정상 (Phase 1 reconnect 로직 KIS 호환 확인)
**변경 파일**: 0개 (work 폴더 untracked)
**비고**: `01_plan.md` 가 AC 체크리스트 초안 상태. `## 구현 계획` 부재 → `/plan 105` 실행하여 구체 구현 계획으로 확장 필요.

### 2026-04-27

**현황**: 0/6 완료 (구현 계획 합의 완료, Stage 1 착수 대기)
**완료된 항목**:
- (없음 — AC 는 모두 운영/실측 단계)
**미완료 항목**:
- KIS 모의계좌 API 연결 정상 (인증/주문/체결/조회)
- 4주 (20 거래일) 실측 운영 로그
- 주문 건수 N ≥ 100 기록
- 모의계좌 체결 vs 자체 체결 시뮬 추적 오차 < 0.5%
- kill-switch 자동 트리거 3종 KIS 환경에서 동작 검증
- WS 단절 자동 재연결 정상
**변경 파일**: 1개 (`docs/work/active/000105-phase2-paper-live/01_plan.md` — `## 구현 계획` 섹션 추가)
**비고**: `/plan` (= `/oh-my-claudecode:ralplan --consensus` deliberate) loop 2회로 합의. Planner v2 + Architect ARCHITECTURALLY SOUND + Critic APPROVE. 핵심 결정: Option A' (self-sim single-pass) 채택, fan-out 폐기. Stage 7a (PR 머지) / Stage 7b (운영 4주 별도 issue) 분리. Architect 인계 노트 6건 (market_state 인자, order_acked origin, PaperBroker fallback guard, strategy_returns_export 시점, R2 파라미터화, WAL 동시쓰기 정책) 은 해당 Stage 위치에 인라인 blockquote 로 부착.

### 2026-04-27 (PR 직전 검증 + 추가 fix)

**현황**: 3/6 PR 게이트 충족 (AC1·AC5·AC6 코드+테스트 green), 3/6 운영 게이트 (AC2·AC3·AC4) 는 #133 분리.

**team 실행 결과**:
- 3 worker (sonnet executor) 가 19 task 의존성 그래프로 분담 → 전체 1290 pytest pass (Phase 2 신규 17건 + Phase 1 회귀 + 기존 모두), check_invariants --strict PASS (113 notes), 4 .ai.md 갱신, 02_implementation.md 작성

**PR 직전 직접 실증 + 추가 fix 5건**:
1. `src/brokers/kis/async_adapter.py::get_balance()` — pydantic schema 가 소문자 필드 (`dnca_tot_amt`) 로 deserialize 하는데 대문자 (`DNCA_TOT_AMT`) 로 lookup 하던 #73 잠재 버그 수정. KRW 잔고 1천만원 정확 출력 확인.
2. `scripts/kis_paper_smoke.py` — 변수명 `KIS_APP_*` → 프로젝트 표준 `HANTOO_FAKE_*` 통일 (`src/brokers/config.py` 와 일치). paper-vs-live credit_number 분기 추가.
3. `scripts/live_run.py` — `_build_kis_adapter()` 헬퍼 추가 + `run_shadow_loop` 에 `kis_adapter` 전달. (이전엔 `kis-paper-shadow` 모드가 ValueError 로 즉시 crash하는 critical 결함)
4. `src/live/loop.py::run_shadow_loop` — `kis_adapter` 인자 추가 (Optional, default None — 후방 호환).
5. `.github/workflows/kis-paper-nightly.yml` — env 변수명 `HANTOO_FAKE_*` 통일 + cron 시간을 KRX 영업시간 중 (KST 월~금 10:00 = UTC 01:00) 으로 변경. e2e 테스트 2종도 같이 fix.

**실증 결과**:
- KIS 모의계좌 인증 OK (cross-process file lock 포함)
- REST 통신 + retry 3회 백오프 OK
- 잔고 조회 OK (10,000,000 KRW 정확)
- 주문 path: 영업시간 외 거부 (40570000 장종료) — KIS 정상 거동, nightly cron(KST 10:00)이 영업시간에 자동 검증

**자동화 완성**:
- GitHub Secret 3개 (`HANTOO_FAKE_API_KEY/SECRET/CREDIT_NUMBER`) 사용자 등록 완료
- nightly E2E workflow가 매주 월~금 KST 10:00 자동 발화 → 한국투자 모바일 앱 모의투자 메뉴에서 매일 005930 1주 매수/매도 거래 기록 확인 가능

