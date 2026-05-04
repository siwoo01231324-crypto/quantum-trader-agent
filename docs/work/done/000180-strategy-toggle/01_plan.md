# 01_plan — #178 + #180 번들 (전략 카탈로그 + ON/OFF 토글)

## 스코프

**번들 PR**: #178 (전략 카탈로그 페이지) + #180 (ON/OFF 토글) 한 번에. #178 의 후행이 #180 이라 카탈로그 카드 위에 토글 스위치를 그대로 얹는 구조 — 분리하면 카탈로그 페이지 머지 → 다음 PR 에서 카드 재구성하는 churn 발생.

## 사용자 결정 사항 (2026-05-05)

LLM 위임 금지 영역 (CLAUDE.md 불변식 6 — 주문 실행 결정).

### D1. 비활성 시 포지션 처리 → **즉시 청산**
toggle OFF 클릭 → 보유 포지션 시장가 매도. "자연 종료" 옵션은 v1 에서 제외.

### D2. UX → **경고 다이얼로그 1단계**
`"S2c 비활성 시 보유 포지션 N개 즉시 청산됩니다. 계속하시겠습니까?"`. 사용자 확인 후 POST 발사. 취소 시 토글 롤백.

### D3. 카탈로그 페이지 → **#178 AC 풀 구현 + 카드에 토글 스위치 통합**
별도 페이지 분리 없이 `/strategies` 한 곳에서 카탈로그 카드 + 각 카드 토글.

## #178 AC 체크리스트

- [ ] frontmatter 누락 필드 보강 — 5종 전략 (`mdd_bt`, `annual_return_bt`, `backtest_period`, `last_updated`)
- [ ] note-schemas.md Strategy 섹션에 신규 optional 필드 추가
- [ ] `GET /api/strategies` REST — frontmatter → JSON 응답
- [ ] `GET /strategies` HTML 페이지 — 카드 그리드 (id, name, instruments, timeframe, sharpe_bt, mdd_bt, status)
- [ ] 카드 클릭 시 전략 상세 페이지 이동 → **본 PR 에서는 placeholder anchor 만 (별도 이슈에서 상세 구현)**
- [ ] 단위 테스트 (FastAPI TestClient + frontmatter mock)

## #180 AC 체크리스트

- [x] `AsyncStrategyOrchestrator.enable_strategy(id)` / `disable_strategy(id)` 추가
- [x] WAL `strategy_toggled` 이벤트 (`EVENT_STRATEGY_TOGGLED`, `StrategyToggledPayload`)
- [x] 비활성 시 신규 시그널 차단 (`run_bar` 가 `_disabled` skip)
- [x] **기존 포지션 즉시 청산** — `disable_strategy(id, positions=...)` 가 `OrderIntent(side='sell')` list 반환
- [ ] `POST /api/strategies/{id}/toggle` REST — body `{enabled: bool}`
- [ ] HTML 토글 스위치 + **경고 다이얼로그** (D2)
- [ ] 단위 테스트 (orchestrator + REST + UI HTML)

## 작업 순서

1. ✅ Orchestrator API + WAL 이벤트 + 16 tests (완료)
2. note-schemas.md + 5 strategy specs frontmatter 보강
3. `src/dashboard/strategy_catalog.py` — frontmatter 로더 + 카탈로그 enrichment
4. `GET /api/strategies` + tests
5. `GET /strategies` HTML + tests
6. `POST /api/strategies/{id}/toggle` + tests
7. UI: 카드 그리드 + 토글 스위치 + confirm dialog
8. `.ai.md` 갱신 (`src/dashboard/`, `src/portfolio/`, `docs/specs/strategies/`)
9. `02_implementation.md` 작성

## 다음 단계

`/plan` 또는 `/ralplan` 단계는 생략 — 작업 단위가 명확하고 사용자가 즉시 진행 지시.
