# siw-claude-template

Claude Code + GitHub Issues 기반 개발 워크플로우 템플릿.

이슈 생성 → 워크트리 분기 → 구현 → PR → 정리까지 모든 단계를 슬래시 커맨드로 자동화합니다.

---

## 포함 내용

| 범주 | 내용 |
|------|------|
| **슬래시 커맨드** | `/bi` `/si` `/ri` `/plan` `/fi` `/ci` `/drop-issue` `/update-changelog` |
| **서브에이전트** | plan-reviewer, code-architecture-reviewer, documentation-architect, refactor-planner, code-refactor-master, frontend-error-fixer, web-research-specialist |
| **보안 훅** | PostToolUse 시크릿 필터 (API키·토큰 자동 탐지) |
| **CI 스크립트** | 불변식 검사, 금지 파일 형식 커밋 차단 |
| **GitHub 자동화** | 이슈→칸반 보드 자동 이동 (Backlog→InProgress→InReview) |
| **이슈·PR 템플릿** | feature / bug / chore |

---

## 빠른 시작

```bash
# 1. 이 템플릿으로 새 레포 생성 (GitHub UI 또는 gh CLI)
gh repo create my-project --private --template siw/siw-claude-template --clone
cd my-project

# 2. 초기화 스크립트 실행
bash setup.sh

# 3. GitHub Project 보드 연결 (docs/onboarding/getting-started.md 참고)
```

자세한 설정은 **`docs/onboarding/getting-started.md`** 를 참고하세요.

---

## 커맨드 치트시트

| 커맨드 | 역할 |
|--------|------|
| `/bi` | 새 이슈 Backlog 생성 |
| `/si <이슈번호>` | 이슈 작업 시작 (워크트리·브랜치 생성) |
| `/ri` | 세션 재시작 시 현황 복구 |
| `/plan` | 구현 계획 작성 |
| `/fi` | 완료 커밋·PR 생성 |
| `/ci` | PR 머지 후 정리 |
| `/drop-issue` | 이슈 중도 포기 |
| `/update-changelog` | CHANGELOG 업데이트 |

---

## 지식볼트 · 온톨로지

`docs/` 는 Obsidian 볼트로 열 수 있으며, 프론트매터 기반 노트가 RDF 온톨로지로 동기화된다.

- **볼트 오픈**: Obsidian → "Open folder as vault" → `docs/` 선택
- **구조**
  - `docs/schemas/note-schemas.md` — 7개 타입 프론트매터 규약
  - `docs/specs/{strategies,signals,risk-rules,instruments}/` — 인스턴스 노트
  - `docs/ontology/` — OWL 온톨로지 (`trading.ttl`) + SPARQL 쿼리
  - `docs/dashboards/` — Dataview 대시보드
- **CLI**
  ```bash
  python scripts/check_invariants.py          # 스키마·링크·TTL 검증 (warn)
  python scripts/ontology_sync.py --write     # instances.ttl 재생성
  ```
- **Phase**: Phase 1 볼트 세팅 → Phase 2 스키마·샘플 → Phase 3 온톨로지 → Phase 4 Dataview → Phase 5 CI·온보딩

자세한 사용법은 `docs/onboarding/obsidian-setup.md` 참고.
