# quantum-trader-agent — Claude Code 가이드

> 세션 시작 시 이 파일을 먼저 읽는다. 지도(map)다. 백과사전이 아니다.

## 시작 전 필수 확인 순서
1. `gh issue list --assignee @me` — 내 담당 이슈 확인
2. `AGENTS.md` — 레포 전체 목차·불변식·규칙
3. 작업 대상 디렉토리의 `.ai.md` — 목적·구조·역할
4. `docs/work/active/` — 현재 진행 중인 작업 내역 (있는 경우)

## 아키텍처 불변식 (위반 시 CI 차단)

`scripts/check_invariants.py --strict` 와 `.github/workflows/ontology-check.yml` 가 강제한다.

```
1. docs/**/*.md (.ai.md·dashboards·schemas·ontology 제외) 는 프론트매터 `type` 필수
2. 프론트매터 `id` 는 파일명과 일치 (work-done 제외)
3. 본문 `[[위키링크]]` 대상 노트는 실제 존재해야 함 (inline/fenced 코드 제외)
4. docs/ontology/trading.ttl 은 rdflib 로 파싱 가능해야 함
5. .draft.md 는 정식 노트 검증 대상에서 제외 (#53, 머지 전 승격 필요)
6. 주문 실행·리스크 결정을 LLM 에 위임 금지 — LLM 은 문서·설계 보조만
7. 새 전략·신호·리스크·종목 노트는 docs/schemas/note-schemas.md 의 스키마 준수
```

## 레포 규칙
```
1. 5MB 초과 파일 커밋 시 컨펌 필요
2. *.pdf, *.csv, *.pkl, *.parquet 커밋 금지 (.gitignore 적용)
3. 모든 디렉토리에 .ai.md 포함 — 목적·구조·역할 기술
4. 작업 전 해당 디렉토리의 .ai.md 확인
5. 작업 완료 후 .ai.md 최신화 필수 (생략 시 작업 미완료)
```

## 작업 흐름
1. 해당 디렉토리 `.ai.md` 읽기
2. GitHub Issue body에서 AC 확인 (`docs/specs/`는 기획/기술 설계 문서)
3. 테스트 먼저 작성 → Red → Green → Refactor
4. **완료 후 `.ai.md` 최신화 — 필수, 생략 시 작업 미완료로 간주**
5. 실패 시 → "레포에 무엇이 없었나?" 진단 → 문서 업데이트

## 핵심 문서 위치
- 기능 명세 + AC → `docs/specs/`
- 온보딩·워크플로우 → `docs/onboarding/`
- 작업 내역 → `docs/work/active/` · `docs/work/done/`
- 프론트매터 스키마 → `docs/schemas/note-schemas.md` (12 타입)
- 도메인 온톨로지 → `docs/ontology/trading.ttl` + `queries/*.rq`
- Dataview 대시보드 → `docs/dashboards/`

## 지식볼트 · LLM 레이어
- `docs/` 는 **Obsidian 볼트**. 노트는 `[[id]]` 위키링크로 연결되고 RDF 온톨로지에 동기화됨
- 새 노트 만들 때는 `docs/schemas/note-schemas.md` 스키마 준수
- LLM 에이전트가 볼트를 도구로 사용할 때는 `services/obsidian_mcp/` MCP 서버 경유 (#51)
- **새 에이전트 추가 시 볼트 연결 필수** — in-process 는 `from services.obsidian_mcp import tools`, 외부 프로세스는 `python -m services.obsidian_mcp.server` stdio MCP 로 붙인다. 볼트를 안 쓰는 에이전트라면 `.ai.md` 에 사유 명시
- 백테스트·인시던트 초안 자동 생성은 `services/doc_agent/` (#53) — 출력은 항상 `.draft.md`, 정식 승격은 사람이 rename
- 자동 커밋 금지 — 드래프트도 리뷰 후 수동 커밋

## 새 전략 추가 시 필수 (#70 이후, #78 확장)
- **Protocol 선택** — `Strategy` (sync `on_bar(bar, history, context)`) 또는 `AsyncStrategy` (async `on_bar(ctx)`) 중 하나를 준수 (`src/backtest/protocol.py`).
- **orchestrator 등록** — `AsyncStrategyOrchestrator.register_strategy(strategy_id, strategy)` 호출 필수. 생략 시 tick driver 에 연결되지 않음.
- **일수익률 시계열 export** — `orchestrator.register_strategy_returns(strategy_id, series)` 로 공급 필수. 생략 시 포트폴리오 CVaR·상관·ENB 체크가 침묵함.
- **`docs/specs/strategies/<strategy_id>.md` 스펙 파일** — 프론트매터 `type: strategy`, "리스크 연동" 섹션에 register 호출 방법 명시.
- **단위 테스트 1건** — `tests/test_portfolio_orchestrator_async.py` 또는 전략-전용 테스트에서 수익률 → report 생성까지 검증.
- **상세**: `src/backtest/strategies/.ai.md` "리스크 연동 (필수)" 섹션 참조.

## 조사·리서치 규칙
- 서베이·리서치 등 조사 작업은 팩트에 근거한 내용만 작성한다
- 조사 결과 문서 하단에는 반드시 출처를 명시한다
- **볼트 사전조회 필수**: 새 research 노트 제안 또는 "gap 이 있다" 고 말하기 전에 반드시 아래를 먼저 수행한다. 생략 시 이미 존재하는 내용을 중복 제안하는 사고로 간주 (2026-04-16 #60 재발 방지).
  1. `grep -ri` 또는 MCP `search` 로 `docs/background/` · `docs/specs/` 에서 주제어 검색
  2. 관련 `[[id]]` 후보 3개 이상을 읽어 기존 커버 범위 확인
  3. SPARQL (`trading.ttl`) 또는 `list_notes(type=...)` 로 관련 엔티티 존재 확인
  4. 그 다음에만 "신규 노트 필요" 결론

## 행동 규칙
- `git commit` 전에 항상 사용자에게 먼저 확인한다 ("커밋할까?" 등으로 물어보고 승인 후 실행)
- `git push` 전에 항상 사용자에게 먼저 확인한다 ("푸시할까?" 등으로 물어보고 승인 후 실행)
