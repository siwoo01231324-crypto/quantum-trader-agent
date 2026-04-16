---
type: work-done
id: 000052-frontmatter-migration-01-plan
name: "01_plan — docs 전체 프론트매터 일괄 마이그레이션 (#52)"
status: done
---

# 01_plan — docs 전체 프론트매터 일괄 마이그레이션 (#52)

> ⚠️ `/plan 52` 로 상세 구현 계획 보강 필요.

## 완료 기준 (AC)
- [ ] `docs/**/*.md` (`.ai.md` 제외) 전 노트에 `type` 필드 존재
- [ ] `id` 가 파일명과 일치
- [ ] 주요 상호 참조가 `[[id]]` 위키링크로 전환 (최소 본문당 1건)
- [ ] `scripts/migrate_frontmatter.py` 재실행 idempotent
- [ ] `scripts/check_invariants.py --strict` 통과
- [ ] Obsidian 그래프뷰 orphan 노드 < 5%
- [ ] Dataview 대시보드 4건이 전체 데이터 반영

## 개발 체크리스트
- [ ] 테스트 코드 포함 (migrate 단위 테스트)
- [ ] 각 디렉토리 `.ai.md` 최신화
- [ ] 불변식 위반 없음

## Phase
1. 추가 타입 5종 스키마(runbook/research/onboarding/whitepaper/spec-architecture) + 샘플
2. migrate_frontmatter.py 경로 규칙 작성
3. 일괄 실행 → 리뷰 → 디렉토리별 단위 커밋
4. 본문 위키링크 전환 (regex 보조)
5. CI strict 모드 전환

## 관련 노트 (구현 대상)

- [[frontmatter-guide]]
