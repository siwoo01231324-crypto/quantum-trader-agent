# #192 구현 계획

> 본 plan 은 코드 매핑 (2026-05-06) 결과 기반 갭 분석으로 작성. 이슈 body 의 미래형 표현이 아니라 **현 코드와 AC 사이의 실제 갭** 만 다룬다.

## 갭 분석 — 이미 구현된 부분 vs 미구현

| AC | 현재 상태 | 본 PR 작업 |
|----|-----------|------------|
| AC1: `client_order_id` 에 `{strategy_id}` 임베딩 | `executor.py:_make_key` 가 `f"{strategy_id}:{symbol}:{ts_ms}:{idx}"` 평문 생성 (✅ 이미 됨, paper 경로). Binance route 의 `client_id.py::generate` 는 SHA-256 해싱이라 strategy_id 손실 — **현 executor 는 호출 안 함**. | 검증만 — 36자 길이 제한 위반 시 fail-fast 가드 추가. |
| AC2: WAL payload 에 `strategy_id` | `signal_emitted` 만 포함. `OrderAckedPayload`, `TrackingSamplePayload`, `FillAnomalyPayload` 미포함. | **`strategy_id: str \| None = None` 필드 추가** (default None → schema 호환). executor 가 `intent.strategy_id` 를 페이로드에 주입. |
| AC3: `StrategyPositionStore` | ❌ 미구현 | **신규 모듈** `src/live/strategy_position_store.py`. fill 이벤트 → `dict[strategy_id, dict[symbol, Decimal qty]]` 누적. |
| AC4: `live_run.py` position_provider 와이어링 | `DashboardState.position_provider` 필드만 정의됨, wiring 없음. | `_run_pipeline` 에 store 인스턴스 생성 + broker fill 콜백 등록 + `state.position_provider = store.get_positions` 주입. |
| AC5: 토글 OFF 통합 테스트 | ❌ 미구현 | `tests/test_strategy_disable_liquidation.py` 신규 — fake broker fill → store 누적 → `/api/strategies/{id}/toggle` → liquidation_intents 검증. |
| AC6: WAL replay 호환성 | 검증 필요 | 옛날 페이로드 (`strategy_id` 키 없음) 는 `payload.get("strategy_id")` 로 None 처리. WAL replay 경로에서 fail 안 하는지 회귀 테스트 추가. |

## 변경 파일 목록

### 코어
1. **`src/live/types.py`** — 4 dataclass 에 `strategy_id: str | None = None` 필드 추가
   - `OrderAckedPayload`, `TrackingSamplePayload`, `FillAnomalyPayload`, `StrategyToggledPayload` (이미 있음)
2. **`src/live/executor.py`** — `wal.write(WALEvent(... payload={... "strategy_id": intent.strategy_id}))` 3 곳:
   - `order_acked` (line 99–109)
   - `tracking_sample` (line 154–168) — req 에는 strategy_id 없음 → executor 가 closure 로 전달
3. **`src/live/strategy_position_store.py`** *(신규)* — `StrategyPositionStore`:
   - `record_fill(strategy_id: str, symbol: str, side: str, qty: Decimal)` — 누적 (`buy` +qty, `sell` -qty)
   - `get_positions(strategy_id: str) -> list[tuple[str, float]]` — 0 qty 항목 제외
   - `replay_from_wal(wal: WAL)` — 부팅 시 복구 (fill_received + strategy_id 페이로드 기반)
4. **`scripts/live_run.py`** — `_run_pipeline` 에서:
   - `store = StrategyPositionStore()` 생성
   - WAL replay 로 부팅 시 복구
   - broker fill observer 또는 WAL `fill_received` 핸들러에 `store.record_fill` 등록
   - `dashboard_state.position_provider = store.get_positions`

### 테스트 (TDD: 모듈 만들기 전에 RED 부터)
5. **`tests/live/test_strategy_position_store.py`** *(신규)* — 단위 8건:
   - 빈 스토어 → `[]`
   - buy 누적 → 양수 qty
   - buy + sell → 차감
   - 0 qty 제외
   - 다른 strategy_id 분리
   - WAL replay 복구
   - replay 시 strategy_id 없는 옛 페이로드 graceful
   - Decimal 정밀도 보존
6. **`tests/test_executor.py`** — 기존에 strategy_id WAL 주입 케이스 추가 (1–2건)
7. **`tests/test_strategy_disable_liquidation.py`** *(신규)* — 통합 4건:
   - 모의 broker → fill 2건 (다른 strategy id) → store → `/api/strategies/A/toggle disabled` → A 보유분만 sell intent
   - position 0 시 빈 list
   - position_provider 미연결 시 503 또는 빈 list (현 핸들러 동작 따라)
   - WAL replay 후 store 복구 → 토글 OFF 정상 동작

### 문서
8. **`src/live/.ai.md`** — `strategy_position_store.py` 한 줄 추가 + #192 항목 추가
9. **`src/dashboard/.ai.md`** — position_provider wiring 완료 사실 반영
10. **`src/portfolio/.ai.md`** *(필요 시)* — 변경 없음 예상

## 변경 안 하는 것 (스코프 박스)

- `client_id.py::generate` (SHA 해시화) 는 그대로 — 현 executor 가 호출 안 함. 미래 Binance live 진입 시 별도 이슈.
- `/api/strategies/{id}/toggle` 핸들러 자체 — 이미 position_provider 콜백 호출함, 변경 없음.
- WAL schema_version bump — 신규 필드는 optional default None 이므로 v1 호환. bump 불필요 (옛 replay 가 새 코드로 읽힐 때 .get() 으로 안전).

## TDD 순서

1. **RED** — `test_strategy_position_store.py` 작성 → 모듈 없으니 ImportError
2. **GREEN-1** — `strategy_position_store.py` 최소 구현 → 단위 테스트 통과
3. **RED** — `test_executor.py` 에 strategy_id WAL 주입 케이스 추가 → 실패
4. **GREEN-2** — executor.py + types.py 수정 → 통과
5. **RED** — `test_strategy_disable_liquidation.py` 통합 → 실패
6. **GREEN-3** — `live_run.py` wiring → 통과
7. **REFACTOR** — 회귀 (`pytest tests/`) + check_invariants → green
8. **DOC** — .ai.md 갱신

## 리스크

- **fill 이벤트 소스 결정**: PaperBroker 가 fill 을 어디서 emit 하는지 확인 필요. WAL `fill_received` 만 있으면 store 가 WAL 폴링하는 구조가 합당하지만, observer 패턴 (broker → callback) 이 있으면 더 단순. → 구현 직전 `paper_broker.py` 확인 후 결정.
- **회귀 위험**: `OrderAckedPayload` 는 `__init__` 위치 인자로 사용되는 곳 있을 수 있음 → keyword-only 추가 권장 (kw_only=True 또는 default 값으로).
- **integration 테스트 fixture 복잡성**: TestClient + orchestrator + fake broker 조합. 기존 `test_dashboard_*` 패턴 재활용.

## 후속 (#194 게이트)

본 이슈 머지 시 #194 (DashboardState 라이브 PnL 와이어링) 의 `pnl_by_strategy` 분리 작업이 가능해진다 — 본 PR 의 `strategy_id` WAL 페이로드를 `PnLAggregator` 가 그대로 활용.
