# feat: 전략별 포지션 추적 (strategy_id 태깅) + position_provider 라이브 와이어링

## 사용자 관점 목표
대시보드 카드의 "보유 포지션 N건" 이 실제 KIS 모의계좌 보유분으로 전략별 분리되어 표시. #180 토글 OFF 시 **그 전략이 진입한 포지션만** 즉시 청산 (다른 전략 거 건드리지 X).

## 배경
- #180 의 `disable_strategy(id, positions=[(symbol, qty)])` 는 caller (REST 핸들러) 가 positions 를 주입해야 동작. 현재 `DashboardState.position_provider` 는 콜백 hook 만 있고 실제 KIS broker 와 연결 X.
- BrokerExecutor (#80) 는 머지됨 — `client_order_id` 포맷에 strategy_id 임베딩, fill 이벤트에서 역추출 가능.
- WAL `order_acked` / `fill_received` 페이로드에 strategy_id 가 일관적으로 안 들어가 있을 수 있음 → 스키마 점검 필요.

## 완료 기준
- [x] `OrderIntent` → broker 발주 시 `client_order_id` 에 strategy_id 임베딩 (`executor._make_key` 가 `{strategy_id}:{symbol}:{ts_ms}:{idx}` 평문 prefix — 이미 구현돼있어 검증만)
- [x] WAL 페이로드(`OrderAckedPayload`, `TrackingSamplePayload`, `FillAnomalyPayload`)에 `strategy_id` 필드 추가 — default `None` 으로 schema v1 호환 (bump 불필요)
- [x] In-memory `StrategyPositionStore` — fill 이벤트 → `{strategy_id: {symbol: qty}}` 누적 (`src/live/strategy_position_store.py`)
- [x] `scripts/live_run.py:_run_pipeline` 이 `DashboardState.position_provider = store.get_positions` 로 와이어링 + 부팅 시 WAL replay 복구
- [x] 토글 OFF 통합 테스트: 모의 fill → disable → 그 전략 보유분만 sell intent (`tests/test_strategy_disable_liquidation.py` 4 케이스)
- [x] WAL replay 호환성 — 구버전 페이로드 (`strategy_id` 필드 없음) → `client_order_id` prefix fallback 으로 strategy_id 복원 (`test_replay_legacy_payload_falls_back_to_client_order_id_parse`)

## 의존성
- 선행: #178 + #180 번들 PR (position_provider hook 정의됨), #80 BrokerExecutor (CLOSED)
- 후속: #143 Phase1 shadow daemon 머지 후 통합 테스트 추가

## 작업 내역
- 2026-05-06: /si 192 — 워크트리 + 브랜치 생성, assign 완료, 보드 In Progress.
- 2026-05-06: 코드 매핑 → 갭 분석 (이슈 body 의 미래형 표현이 아니라 실제 미구현 부분만 추림)
- 2026-05-06: TDD RED-1 (`test_strategy_position_store.py` 10 케이스, ImportError 의도된 실패)
- 2026-05-06: GREEN-1 (`src/live/strategy_position_store.py` 신규: register_order/record_fill/replay_from_wal/ingest_fill_event) → 10/10 pass
- 2026-05-06: RED-2 (`test_executor.py` +2 케이스: WAL strategy_id, position_store register) / GREEN-2 (`types.py` 3 페이로드 + `executor.py` 페이로드 주입 + position_store 인자) → 9/9 pass
- 2026-05-06: RED-3 / GREEN-3 (`test_strategy_disable_liquidation.py` 4 통합 + `scripts/live_run.py:_run_pipeline` 와이어링) → 4/4 pass
- 2026-05-06: REFACTOR — `.ai.md` 갱신, 풀 회귀 1965 pass / 12 skip / 0 fail (156s), check_invariants 175 노트 통과
- 2026-05-06: rebase onto origin/master `5e8009d` (Telegram 봇 — 무관 영역, 충돌 없음)
