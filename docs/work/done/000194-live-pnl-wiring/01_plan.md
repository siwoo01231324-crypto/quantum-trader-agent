# #194 구현 계획

> 코드 매핑 (2026-05-06) 결과 기반 갭 분석. 이슈 body 의 미래형 표현이 아닌 **현 코드와 AC 사이의 실제 갭** 만 다룬다.

## 갭 분석

| AC | 현 상태 | 본 PR 작업 |
|----|---------|------------|
| AC1: `PnLAggregator` (cum + daily + monthly + by_strategy) | ❌ 미구현 | 신규 모듈 `src/live/pnl_aggregator.py` |
| AC2: KST 09:00 자동 일일 리셋 + 부팅 시 복구 | ❌ 미구현 | fill ts 의 KST business date 기준 분류 + property 호출 시 reset 체크 (timer 불필요) |
| AC3: `live_run.py` broker fill stream → aggregator → state | ❌ 미구현 | `_run_pipeline` 에서 aggregator 인스턴스 + WAL replay + wal_observer 가 ingest 호출 + state.pnl_* 갱신 |
| AC4: `_enriched_catalog()` 에 `pnl_today` 필드 추가 + HTML 표시 | ❌ 미구현 | 카드 dict 에 `pnl_today: float` 머지 + 카드 렌더링 + 음/양수 색상 |
| AC5: 단위 테스트 (시뮬 fill, KST 경계, 부팅 복구) | ❌ 미구현 | `tests/live/test_pnl_aggregator.py` ~10 케이스 |
| AC6: 통합 테스트 (모의 fill → /api/pnl 정확 값) | ❌ 미구현 | `tests/test_dashboard_pnl_integration.py` 4 케이스 |

## 설계 — `PnLAggregator`

### 책임
- fill 이벤트 → realized PnL 계산 (평균매수가 추적)
- 누적: `realtime` (cum since boot) / `daily` / `monthly` / `by_strategy` / `daily_by_strategy`
- KST 09:00 business date 기준 daily/monthly 분류 (자정 12시 아님)
- 부팅 시 `replay_from_wal(path)` 로 복구
- `ingest_fill_event(event_type, payload)` — `wal_observer` hook (#192 store 패턴 동일)

### Realized PnL 계산
```
buy:  cost_basis 갱신, realized = -fee
sell: realized = (price - avg_cost) * qty - fee, holdings 차감
```

`_cost_basis: dict[(strategy_id, symbol), (qty, avg_cost)]` Decimal 정밀도

### KST business date
- KST 09:00 이전 → 전 영업일에 속함
- KST 09:00 이후 → 당일 영업일
- fill 의 ts 가 today_business_date 와 같으면 daily 누적, 아니면 무시
- property `daily` / `monthly` 호출 시 `_check_and_reset_if_date_changed()` 한 번 호출 (자정 지나면 자동 0 reset)

### strategy_id 해석 (옵션 A)
- WAL `order_filled` payload 에 `strategy_id` 가 없음 (PaperBroker 미주입)
- `client_order_id` prefix 파싱 (`split(":")[0]`)
- #192 의 `StrategyPositionStore._resolve_strategy` 와 동일 로직 → 헬퍼로 분리 (`src/live/strategy_resolver.py`) 또는 PnLAggregator 내부 재구현. 단순화: 내부 재구현 (작은 함수 1개)

## 변경 파일

### 코드 (1 신규 + 2 수정)
1. **`src/live/pnl_aggregator.py`** *(신규)* — `PnLAggregator` 클래스
2. **`src/dashboard/app.py`**:
   - `DashboardState.pnl_aggregator: PnLAggregator | None = None` 필드 추가 (또는 기존 pnl_* 필드를 aggregator property 로 위임)
   - `_enriched_catalog()` 에 `pnl_today = aggregator.daily_for(sid)` 머지
   - HTML 카드 렌더링에 "오늘 +N원" 표시 (음수 빨강, 양수 초록)
   - `/api/pnl` 응답에 `by_strategy` 추가 (선택, AC 외)
3. **`scripts/live_run.py:_run_pipeline`**:
   - `aggregator = PnLAggregator()` 생성
   - 부팅 시 `replay_from_wal(config.wal_path)` 로 복구
   - `dashboard_state.pnl_aggregator = aggregator`
   - `wal_observer` 가 timeline + store + aggregator 셋 다 호출
   - `state.pnl_realtime/daily/monthly` 가 aggregator property 와 동기화 (callback or property)

### 테스트 (2 신규)
4. **`tests/live/test_pnl_aggregator.py`** *(신규, ~10 케이스)*
   - 빈 aggregator → 0
   - buy → realized = -fee
   - buy then sell → realized = profit
   - 평균매수가 정확성 (3 buys 다른 가격 → 정확한 avg)
   - 다른 strategy 분리
   - daily 가 today fill 만 누적 (ts 검사)
   - KST 09:00 경계: 어제 fill / 오늘 fill 분류
   - WAL replay 복구
   - daily property 호출 시 자정 지나면 reset (now_kst monkeypatch)
   - daily_for(strategy_id) 정확
5. **`tests/test_dashboard_pnl_integration.py`** *(신규, 4 케이스)*
   - aggregator 주입 → fill → `/api/pnl` 정확
   - 다른 전략 fill → 카드별 pnl_today 분리
   - 빈 aggregator → 0/0/0
   - aggregator 미연결 → 503 또는 default 0 (현 동작 따라)

### 문서
6. **`src/live/.ai.md`** — pnl_aggregator.py 추가 + #194 항목
7. **`src/dashboard/.ai.md`** — pnl_aggregator 와이어링 사실 + 카드 pnl_today

## TDD 순서
1. RED-1 → GREEN-1: pnl_aggregator 단위
2. RED-2 → GREEN-2: dashboard pnl_today 카드
3. RED-3 → GREEN-3: live_run.py wiring
4. REFACTOR + 풀 회귀 + .ai.md

## 스코프 박스 (안 함)
- Unrealized PnL (현재가 mark-to-market) — broker.get_positions().unrealized_pnl 별도 활용은 후속 이슈
- WAL `order_filled` payload 에 strategy_id 직접 주입 — broker 인터페이스 변경, 본 PR 은 client_order_id prefix 파싱으로 충분
- `/api/pnl` 의 by_strategy 응답 (선택) — UI 노출 안 하면 굳이
