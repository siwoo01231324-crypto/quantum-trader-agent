# #192 구현 노트

## TDD 순서 (실측)

| 단계 | 내용 | 결과 |
|---|---|---|
| RED-1 | `tests/live/test_strategy_position_store.py` 9 케이스 → ModuleNotFoundError | ✅ 의도된 실패 |
| GREEN-1 | `src/live/strategy_position_store.py` 신규 — `StrategyPositionStore` 클래스 | 9/9 pass |
| RED-2 | `tests/test_executor.py` +2 케이스 (order_acked WAL strategy_id, position_store register) | 2 fail / 7 pass |
| GREEN-2 | `src/live/types.py` 3 dataclass 에 `strategy_id` 필드 + `executor.py` 페이로드 주입 + `position_store` 인자 | 9/9 pass |
| RED-3 | `tests/test_strategy_disable_liquidation.py` 4 통합 케이스 | 4/4 pass (이미 코드상 결합 정상 — store 만 만들면 toggle 는 그대로 작동) |
| GREEN-3 | `scripts/live_run.py:_run_pipeline` 와이어링 + `store.ingest_fill_event` 추가 | 46/46 pass (5 모듈 합산) |
| REFACTOR | 풀 회귀 + .ai.md + check_invariants | (이 노트 작성 중) |

## 변경 파일

### 코드
- **`src/live/strategy_position_store.py`** *(신규)* — `StrategyPositionStore`. `register_order`, `record_fill`, `record_fill_by_client_order_id`, `get_positions`, `replay_from_wal`, `ingest_fill_event`. WAL replay + live 양쪽 모두 같은 ingest 경로 사용.
- **`src/live/types.py`** — `OrderAckedPayload`, `TrackingSamplePayload`, `FillAnomalyPayload` 에 `strategy_id: str | None = None` 추가 (default None → schema v1 호환, schema_version bump 불필요).
- **`src/live/executor.py`** — `execute_intents()` 에 `position_store: StrategyPositionStore | None = None` 인자 추가. 정상 ack 시:
  - `store.register_order(client_order_id, strategy_id)` 호출
  - WAL `order_acked` payload 에 `strategy_id` 직접 주입 (AC2)
  - `tracking_sample` 에도 strategy_id 주입 (closure 로 전달)
- **`scripts/live_run.py:_run_pipeline`** — 부팅 시 `StrategyPositionStore` 인스턴스 생성 → `replay_from_wal(config.wal_path)` → `dashboard_state.position_provider = store.get_positions` 와이어링. `wal_observer` 가 timeline_broker.publish + `store.ingest_fill_event` 둘 다 호출하도록 lambda 를 함수로 승격.

### 테스트
- **`tests/live/test_strategy_position_store.py`** *(신규, 10 케이스)* — 단위.
- **`tests/test_executor.py`** *(+2 케이스)* — order_acked WAL strategy_id 검증 + position_store register 검증.
- **`tests/test_strategy_disable_liquidation.py`** *(신규, 4 케이스)* — store + orchestrator + position_provider 통합. 토글 OFF 시 그 전략 보유분만 sell intent.

### 문서
- **`src/live/.ai.md`** — `strategy_position_store.py` 추가 + #192 섹션
- **`src/dashboard/.ai.md`** — `position_provider` 와이어링 사실 반영 + #192 항목
- **`docs/work/active/000192-strategy-id-tagging/`** — 00_issue.md / 01_plan.md / 02_implementation.md (본 파일)

## 설계 결정

### 옵션 C 채택 (메모리 매핑 + WAL payload 직접 기록)
- **옵션 A** (`client_order_id.split(':')[0]` 만): Binance route 의 `client_id.py::generate` SHA-256 해시화 시 strategy_id 손실 → fragile.
- **옵션 B** (broker 인터페이스 변경): 전체 broker (paper/binance/kis/router) 변경 → 회귀 영향 큼.
- **옵션 C** (현재): WAL payload 에 strategy_id 직접 기록 + store 메모리 매핑 (`register_order`) + client_order_id prefix fallback. 인터페이스 비파괴.

### schema_version bump 안 함
- 신규 `strategy_id` 필드는 default `None` → 옛 페이로드 deserialize 시 자동으로 None.
- `WALEvent(**data)` 가 미정의 키 만나도 TypeError 안 남 (dataclass 가 keyword 만 받으니).
- 따라서 v1 호환. AC6 의 "구버전 페이로드 strategy_id=null" 그대로 충족.

### Store 위치: `src/live/`
- fill 이벤트 처리는 live 레이어 책임. `src/portfolio/` 가 후보지만 그쪽은 신호→intent 책임 (포지션 트래킹 X).
- WAL replay 로 부팅 복구 → live daemon 의 부팅 시퀀스 안에 자연스럽게 들어감.

## 스코프 박스 (안 함)

- `client_id.py::generate` SHA 해시 변환 — 본 PR 의 paper 경로는 `_make_key()` 평문 사용. Binance live 진입은 별도 이슈.
- `OrderRequest` / `OrderAck` / `BrokerFill` 에 strategy_id 필드 추가 — broker 인터페이스 비파괴 원칙.
- WAL schema_version bump — 하위호환 가능하므로 불필요.

## AC 충족 매트릭스

| AC | 상태 | 검증 위치 |
|---|---|---|
| AC1: client_order_id 에 strategy_id 임베딩 | ✅ (이미 구현) | `executor.py:_make_key` 평문 prefix |
| AC2: WAL 페이로드에 strategy_id | ✅ | `tests/test_executor.py::test_order_acked_wal_payload_includes_strategy_id` |
| AC3: StrategyPositionStore | ✅ | `tests/live/test_strategy_position_store.py` 10 케이스 |
| AC4: live_run position_provider 와이어링 | ✅ | `scripts/live_run.py:_run_pipeline` (간접 검증: 통합 테스트 + 회귀) |
| AC5: 토글 OFF 통합 테스트 | ✅ | `tests/test_strategy_disable_liquidation.py` 4 케이스 |
| AC6: WAL replay 호환성 (구버전 strategy_id=null) | ✅ | `tests/live/test_strategy_position_store.py::test_replay_legacy_payload_falls_back_to_client_order_id_parse` |

## 후속 (#194 게이트 풀림)

본 PR 머지 시 #194 의 `pnl_by_strategy: dict[str, float]` 분리는 본 PR 의 WAL `strategy_id` 페이로드를 `PnLAggregator` 가 그대로 활용해 구현 가능.
