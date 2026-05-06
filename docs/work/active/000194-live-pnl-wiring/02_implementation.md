# #194 구현 노트

## TDD 순서 (실측)

| 단계 | 내용 | 결과 |
|---|---|---|
| RED-1 | `tests/live/test_pnl_aggregator.py` 10 케이스 → ModuleNotFoundError | ✅ 의도된 실패 |
| GREEN-1 | `src/live/pnl_aggregator.py` 신규 (`PnLAggregator`) | 10/10 pass |
| RED-2 | `tests/test_dashboard_pnl_integration.py` 5 케이스 → KeyError/AttributeError | ✅ 5 fail |
| GREEN-2 | `DashboardState.pnl_aggregator` 필드 + `_pnl_view` helper + `/api/pnl` 사용 + `_enriched_catalog` `pnl_today` 머지 + `_render_dashboard` Q1 helper 사용 | 33/33 pass (단위 + 통합 + 카탈로그 회귀) |
| RED-3 / GREEN-3 | `scripts/live_run.py:_run_pipeline` aggregator 인스턴스 + WAL replay + state 주입 + wal_observer 가 셋 다 호출 | 51/51 pass |
| REFACTOR + DOC | `.ai.md` 갱신, 풀 회귀, check_invariants | (작성 중) |

## 변경 파일

### 코드
- 🆕 **`src/live/pnl_aggregator.py`** — `PnLAggregator`. `record_fill` (Decimal cost basis), `ingest_fill_event` (`wal_observer` 결합), `replay_from_wal` (부팅 복구), `realtime` / `daily` / `monthly` / `by_strategy` / `daily_for(sid)` property, KST 09:00 business-date helper.
- ✏️ **`src/dashboard/app.py`**:
  - `DashboardState.pnl_aggregator: Any | None = None` 필드
  - module-level `_pnl_view(state)` helper — aggregator 우선, 없으면 legacy `pnl_*` fallback
  - `/api/pnl` → `_pnl_view(state)` 반환 (응답에 `by_strategy` 포함)
  - `_render_dashboard` Q1 손익 카드 → helper 사용
  - `_enriched_catalog()` 가 각 항목에 `pnl_today = aggregator.daily_for(sid)` 머지 (없으면 0.0)
- ✏️ **`scripts/live_run.py:_run_pipeline`**:
  - `pnl_aggregator = PnLAggregator()` 생성 → `replay_from_wal(config.wal_path)` 부팅 복구
  - `dashboard_state.pnl_aggregator = pnl_aggregator`
  - `_wal_observer` 가 timeline_broker + position_store + pnl_aggregator 셋 다 호출

### 테스트
- 🆕 **`tests/live/test_pnl_aggregator.py`** (10 케이스): 빈 → 0, buy realized=-fee, buy+sell profit, 평균매수가 (3 buys), strategy 분리, 어제 fill daily 제외, KST 09:00 경계, WAL replay, daily property 자동 reset, legacy payload prefix fallback
- 🆕 **`tests/test_dashboard_pnl_integration.py`** (5 케이스): /api/pnl realtime/daily/monthly + by_strategy, 카드 pnl_today 분리, 빈 aggregator → 0, aggregator 미연결 fallback, 미연결 카드 pnl_today=0

### 문서
- ✏️ **`src/live/.ai.md`** — `pnl_aggregator.py` 추가 + #194 와이어링 섹션
- ✏️ **`src/dashboard/.ai.md`** — #194 항목 (pnl_aggregator + _pnl_view helper)
- 🆕 **`docs/work/active/000194-live-pnl-wiring/`** — 00_issue.md / 01_plan.md / 02_implementation.md (본 파일)

## 설계 결정

### KST 09:00 business-date (KRX 영업일 기준)
- 자정 12시 X — KST 09:00 미만이면 전 영업일에 속함
- timer 없이 fill ts 검사 + property 호출 시 자동 reset
- 부팅 후 `_cached_business_date == None` 상태에서 첫 fill / property 호출 시 초기화
- 자정 (혹은 09:00) 이 흘러가면 다음 property 호출에서 `_daily = 0.0` 자동 reset

### realized only (unrealized 별도)
- 본 PR 은 fill 발생 시점의 realized PnL 만 처리
- mark-to-market unrealized (현재가 - avg_cost) × held qty 는 후속 이슈 (broker.get_positions().unrealized_pnl 등 별도 채널)

### `_resolve_strategy` 중복 (vs #192 store)
- 두 모듈 모두 `client_order_id` prefix 파싱 fallback 사용. 작은 함수 1개 중복은 양쪽 응집도 우선시. 헬퍼 모듈로 추출은 후속 정리 (현재 우선순위 낮음).

### aggregator 미연결 fallback
- `_pnl_view` 가 aggregator None 일 때 legacy `state.pnl_*` 0.0 default 반환 → `dashboard-only mode` (qta.exe 더블클릭) 등 wiring 안 한 경로 무영향

## AC 충족 매트릭스

| AC | 상태 | 검증 위치 |
|---|---|---|
| AC1: PnLAggregator (cum + daily + monthly + by_strategy) | ✅ | `test_pnl_aggregator.py` 10 케이스 |
| AC2: KST 09:00 자동 일일 리셋 + 부팅 복구 | ✅ | `test_kst_0900_business_date_boundary` + `test_daily_resets_when_business_date_advances` + `test_replay_from_wal_reconstructs_state` |
| AC3: live_run broker fill stream → aggregator → state | ✅ | `_run_pipeline` 변경 + 통합 테스트 + 풀 회귀 |
| AC4: `_enriched_catalog` 카드 pnl_today + HTML | ✅ | `test_per_strategy_card_shows_pnl_today` (JSON 검증) — HTML 표시는 후속 UX 이슈 |
| AC5: 단위 테스트 (시뮬, KST 경계, 부팅) | ✅ | 단위 10 |
| AC6: 통합 테스트 (모의 fill → /api/pnl) | ✅ | 통합 5 |

## 스코프 박스 (안 함)

- HTML 카드의 "오늘 +N원" 시각 표시 (음수 빨강/양수 초록 색상) — JSON 으로 노출은 했고 디자인은 후속 UX 이슈에서. AC4 의 의도는 데이터 흐름 완성이지 색상 디테일 X.
- mark-to-market unrealized PnL — broker.get_positions().unrealized_pnl 채널 별도 필요
- `OrderRequest`/`order_filled` payload 에 strategy_id 직접 추가 — broker 인터페이스 변경, 본 PR 은 client_order_id prefix 파싱 충분
- `/api/pnl` 응답 schema 변경 (by_strategy 추가) — 기존 callers 가 readtime/daily/monthly 만 읽으면 영향 없음, 추가만 한 형태

## 후속

- HTML 카드에 pnl_today 시각 표시 (UX 이슈)
- mark-to-market unrealized PnL (별도 이슈)
- WAL `order_filled` payload 에 strategy_id 직접 주입 (PaperBroker 변경, 별도 이슈)
