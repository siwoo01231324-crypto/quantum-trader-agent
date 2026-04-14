# 01_plan — Obsidian 지식볼트 + 트레이딩 온톨로지 구현

> ⚠️ 이 문서는 `/start-issue`가 생성한 **AC 체크리스트 초안**이다.
> 구현 시작 전 반드시 `/plan` 커맨드로 구체적 구현 계획을 보강해야 한다.

## 완료 기준 (AC)

- [ ] `docs/` 를 Obsidian으로 열어 그래프뷰에 전략·신호·리스크 노드가 색상별로 표시됨
- [ ] 7개 노트 타입 프론트매터 스키마가 `docs/schemas/note-schemas.md` 에 문서화됨
- [ ] 기존 문서 중 대표 3~5건이 스키마대로 마이그레이션됨
- [ ] `docs/ontology/trading.ttl` 이 rdflib로 파싱 에러 없이 로드됨
- [ ] `python scripts/ontology_sync.py --write` 가 정상 실행되어 `instances.ttl` 생성
- [ ] 최소 3개 SPARQL 쿼리(`live_strategies`, `critical_violations`, `strategy_without_tests`) 가 결과 반환
- [ ] `docs/dashboards/` 에 Dataview 대시보드 4건 이상, 옵시디언에서 정상 렌더링
- [ ] CI에서 프론트매터 스키마·TTL·링크 무결성 검증이 실패 조건으로 동작
- [ ] `docs/onboarding/` 문서 3건 + `AGENTS.md` 갱신 + 신설 디렉토리 `.ai.md` 완비
- [ ] README에 "지식볼트·온톨로지" 섹션 추가

## 개발 체크리스트

- [ ] 테스트 코드 포함 (`scripts/ontology_sync.py` 단위 테스트, CI 검증 스크립트 테스트)
- [ ] 해당 디렉토리 `.ai.md` 최신화
- [ ] 불변식 위반 없음 (`scripts/check_invariants.py`)

## 다음 단계
1. `/plan 47` 실행 → Phase별 구현 계획 보강
2. Phase 1 착수 전 `docs/ontology/`, `docs/schemas/`, `docs/dashboards/`, `docs/onboarding/` 디렉토리의 `.ai.md` 먼저 계획
3. PR #46(AGENTS.md) 머지 확인 후 본격 작업
