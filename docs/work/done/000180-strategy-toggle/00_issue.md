# feat: 전략 ON/OFF 토글 UI + REST API (runtime orchestrator 제어)

## 사용자 관점 목표
대시보드에서 전략별 토글 → 즉시 활성/비활성. 재시작 없이 runtime 적용.

## 배경
production.yaml 은 시작 시 로드되는 정적 설정. 사용자가 매매 중 특정 전략 끄고 싶을 때 EXE 재시작 부담.

## 완료 기준
- [x] `POST /strategies/{id}/toggle` REST — `enabled: bool` 토글
- [x] `AsyncStrategyOrchestrator` 에 `enable_strategy(id)` / `disable_strategy(id)` 추가 (기존 register_strategy 위에)
- [x] WAL 에 `strategy_toggled` 이벤트 기록 (감사 로그)
- [x] 비활성 시 신규 시그널 차단, 기존 포지션은 청산 정책 (옵션: "즉시 청산" / "자연 종료" → **즉시 청산** 채택, D1)
- [x] HTML 토글 UI + 단위 테스트

## 의존성
- 선행: 전략 카탈로그 페이지 → **본 PR 에 #178 번들로 함께 구현** (별도 이슈 X)

## 작업 내역

본 PR 은 **#178 (전략 카탈로그 페이지) + #180 (ON/OFF 토글)** 을 번들로 처리. #178 의 카드 그리드 위에 #180 의 토글 스위치를 얹는 구조라 분리 시 UI churn 발생 → 사용자 결정으로 통합.

### 사용자 결정 사항 (2026-05-05, LLM 위임 금지 영역)
- **D1**: 비활성 시 → 즉시 청산 (시장가 매도). "자연 종료" 옵션 v1 미포함.
- **D2**: 토글 OFF 클릭 시 `window.confirm` 경고 다이얼로그 1단계.
- **D3**: 카탈로그 페이지 = 본 PR 내 + 카드별 토글 통합.

### 구현
1. **Orchestrator API** (`src/portfolio/_async_orchestrator.py`)
   - `wal_observer` 생성자 인자 추가
   - `enable_strategy` / `disable_strategy(positions=...)` / `is_enabled` / `disabled_strategies`
   - `run_bar` 가 `_disabled` skip → 신규 시그널 차단
   - `disable_strategy` 가 보유 포지션에 대해 `OrderIntent(side='sell')` list 반환 (D1)
2. **WAL 감사** (`src/live/types.py`)
   - `EVENT_STRATEGY_TOGGLED = "strategy_toggled"` + `StrategyToggledPayload`
3. **카탈로그 로더** (`src/dashboard/strategy_catalog.py` 신규)
   - `docs/specs/strategies/*.md` frontmatter → 정규화 dict list. JSON-safe (date → ISO string)
4. **REST + UI** (`src/dashboard/app.py`)
   - `GET /api/strategies` JSON · `GET /strategies` HTML 카탈로그 · `POST /api/strategies/{id}/toggle`
   - 메인 대시보드 (`/`) 에 카탈로그 인라인 임베딩 + 단독 페이지 양쪽 지원
   - 공유 CSS/JS 상수 추출 (`_STRATEGY_CARD_CSS`, `_STRATEGY_TOGGLE_JS`)
   - JS confirm 다이얼로그 (D2) + 토글 실패 시 롤백
5. **Schema 보강** (`docs/schemas/note-schemas.md`, 5종 spec 파일)
   - 신규 optional 필드: `mdd_bt`, `annual_return_bt`, `backtest_period`, `last_updated`, `summary_ko`
   - `summary_ko` 는 카드용 한국어 평이한 설명 (RSI 다이버전스, MACD 모멘텀 등 평이한 용어로 2-3문장)

### 테스트 (41 신규 + 65 회귀, 합 106 통과)
- `tests/test_strategy_toggle.py` — 16: 활성/비활성 상태, idempotent, run_bar gating, 청산 intents, WAL audit, observer 예외 swallow
- `tests/test_strategy_catalog_loader.py` — 7: 정규화, 파일 필터, missing dir, 실 repo 5종 smoke
- `tests/test_dashboard_strategies.py` — 18: JSON shape, HTML rendering, 토글 enable/disable, position_provider, 404/422/503

### 후속 분리된 이슈 (사용자 요구로 #133 운영 전 구현)
- #191: 전략 상세 페이지 `/strategies/{id}` (#178 본문에 후행으로 명시되어 있던 부분)
- #192: 전략별 포지션 추적 (`strategy_id` 태깅) — `position_provider` 의 진짜 백엔드
- #193: 전략별 체결 이력 필터
- #194: DashboardState 라이브 PnL 와이어링 (KST 09:00 일일 리셋)
