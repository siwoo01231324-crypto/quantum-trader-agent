# 02_implementation — #178 + #180 번들

## 사용자 결정 사항 (LLM 위임 금지 영역, CLAUDE.md 불변식 6)

| ID | 결정 | 사유 |
|----|------|------|
| D1 | 비활성 시 **즉시 청산** (시장가 매도) | 토글 OFF 는 긴급 상황 가정. v1 에서 "자연 종료" 옵션 미포함 |
| D2 | OFF 클릭 시 **경고 다이얼로그 1단계** (`window.confirm`) | 보유 포지션 즉시 청산 사실을 사용자가 한 번 확인 |
| D3 | 카탈로그 페이지 = **#178 본 PR 내** + 카드별 토글 | 별도 PR 머지 시 UI churn — 번들이 자연스럽다 |

## 변경 파일

### 새 파일
- `src/dashboard/strategy_catalog.py` — frontmatter 로더 (`load_strategy_catalog`)
- `tests/test_strategy_toggle.py` — orchestrator 토글 + WAL 감사 (16 tests)
- `tests/test_strategy_catalog_loader.py` — 카탈로그 로더 (7 tests)
- `tests/test_dashboard_strategies.py` — REST + HTML + 토글 (18 tests)

### 수정
- `src/live/loop.py` — `ShadowConfig.on_orchestrator_ready` 콜백 추가. `_load_orchestrator` 직후 호출. 예외 swallow + log warn.
- `scripts/live_run.py` — `_run_pipeline` 에서 `config.on_orchestrator_ready = lambda orch: setattr(dashboard_state, "orchestrator", orch)` 와이어링. **이게 없으면 qta.exe 정식 흐름에서도 토글 503 으로 죽음** — 본 PR 의 토글이 진짜로 동작하기 위한 필수 라스트마일.
- `tests/test_loop.py` — `on_orchestrator_ready` 정상 호출 + 콜백 예외 swallow 2 tests.
- `src/live/.ai.md` — `#180 토글 와이어링` 섹션 추가.
- `src/portfolio/_async_orchestrator.py`
  - `wal_observer` 생성자 인자 추가
  - `_disabled: set[str]`, `enable_strategy`, `disable_strategy`, `is_enabled`, `disabled_strategies` 추가
  - `run_bar` 이 `_disabled` skip 하도록 targets 필터 보강
  - `_emit_strategy_toggled` 헬퍼 (observer 예외 swallow)
- `src/live/types.py`
  - `EVENT_STRATEGY_TOGGLED = "strategy_toggled"` 상수
  - `StrategyToggledPayload` dataclass (strategy_id, enabled, actor)
- `src/dashboard/app.py`
  - `DashboardState`: `orchestrator`, `specs_dir`, `position_provider` 필드
  - `_render_strategies` + `_strategy_card` + `_fmt_metric` HTML 헬퍼
  - 신규 엔드포인트 3개: `GET /api/strategies`, `GET /strategies`, `POST /api/strategies/{id}/toggle`
- `src/dashboard/.ai.md` — Endpoints 표 + "전략 카탈로그 + 토글" 섹션 추가
- `src/portfolio/.ai.md` — "Runtime ON/OFF 토글 (#180)" 섹션 추가
- `docs/schemas/note-schemas.md` — Strategy 선택 필드 4종 추가 (`mdd_bt`, `annual_return_bt`, `backtest_period`, `last_updated`)
- `docs/specs/strategies/.ai.md` — 카탈로그 데이터 소스 설명 + 신규 필드 표
- `docs/specs/strategies/{breakout-donchian, meanrev-pairs, momo-btc-v2, momo-kis-v1, momo-vol-filtered}.md` — 5종 frontmatter 보강 (값 모르면 null, `last_updated: 2026-05-05`)

## 새 데이터 흐름

```
사용자 → /strategies 카드 OFF 클릭
        ↓
JS confirm("...즉시 청산됩니다. 계속?") (D2)
        ↓ (확인)
POST /api/strategies/{id}/toggle {enabled: false}
        ↓
DashboardState.position_provider(id) → [(symbol, qty), ...]
        ↓
orchestrator.disable_strategy(id, positions=...)
        ├→ _disabled.add(id)         (run_bar 가 skip → 신규 시그널 차단)
        ├→ wal_observer(strategy_toggled) (감사 로그)
        └→ list[OrderIntent(side='sell', reason='strategy_disabled_liquidation')]
        ↓
응답 {ok, strategy_id, enabled, liquidation_intents}
        ↓
JS: 청산 의도 N건 alert + location.reload
```

**broker 로의 실제 발주는 본 PR 범위 외** — `liquidation_intents` 를 소비하는 live loop 통합은 후속 wiring (#80 BrokerExecutor 후속).

## 테스트 결과

```
tests/test_strategy_toggle.py             16 passed
tests/test_strategy_catalog_loader.py      7 passed
tests/test_dashboard_strategies.py        18 passed
tests/test_dashboard.py (regression)      16 passed
tests/test_dashboard_ws_timeline.py       14 passed
tests/test_portfolio_orchestrator*.py     26 passed
tests/test_wal.py                          9 passed
─────────────────────────────────────────────────────
                                         106 passed
```

## AC 매핑

### #178 AC
- [x] `GET /api/strategies` REST → frontmatter JSON
- [x] `GET /strategies` HTML 카드 그리드 (id, name, instruments, timeframe, sharpe_bt, mdd_bt, status)
- [x] 카드 anchor → `/strategies/{id}` (상세 페이지는 별도 후속 이슈)
- [x] 단위 테스트 (FastAPI TestClient + frontmatter mock)
- [x] frontmatter 누락 필드 보강 5종 — `mdd_bt`, `annual_return_bt`, `backtest_period`, `last_updated`

### #180 AC
- [x] `POST /api/strategies/{id}/toggle` REST
- [x] `enable_strategy` / `disable_strategy` orchestrator 메소드
- [x] WAL `strategy_toggled` 이벤트
- [x] 신규 시그널 차단 + 즉시 청산 (D1)
- [x] HTML 토글 UI + 경고 다이얼로그 (D2)
- [x] 단위 테스트

## 후속

- 상세 전략 페이지 (`/strategies/{id}`) — 별도 이슈
- live loop 통합: dashboard `liquidation_intents` 응답 → broker 실발주 → fill 모니터
- 자동 토글 (auto-disable on circuit-break) — `actor='auto'` 페이로드 활용
