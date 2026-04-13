# Agents

Claude Code 네이티브 서브에이전트 카탈로그.
`.md` 파일 하나 = 에이전트 하나. Claude가 `description`을 읽고 호출 시점을 자동 판단한다.

---

## 에이전트 목록

### 계획 & 리뷰

| 에이전트 | 역할 | 모델 | 호출 시점 |
|----------|------|------|----------|
| `plan-reviewer` | 구현 계획 검토 — 리스크, 누락 AC, 의존성 지적 | opus | 구현 착수 전 |
| `code-architecture-reviewer` | 코드 품질·아키텍처 일관성 리뷰 | sonnet | 기능 구현 완료 후 |

### 문서화

| 에이전트 | 역할 | 모델 | 호출 시점 |
|----------|------|------|----------|
| `documentation-architect` | `.ai.md` 및 문서 생성/갱신 | sonnet | 구조 변경 후, 작업 완료 후 |

### 리팩토링

| 에이전트 | 역할 | 모델 | 호출 시점 |
|----------|------|------|----------|
| `refactor-planner` | 리팩토링 분석 및 계획 수립 | sonnet | 리팩토링 요청 시 (실행 전) |
| `code-refactor-master` | 리팩토링 실행 및 검증 | sonnet | 계획 승인 후 실행 시 |

### 디버깅 & 리서치

| 에이전트 | 역할 | 모델 | 호출 시점 |
|----------|------|------|----------|
| `frontend-error-fixer` | Next.js/React 빌드·런타임 에러 디버깅 | sonnet | 프론트엔드 에러 발생 시 |
| `web-research-specialist` | 외부 리서치 전담 | sonnet | 기술 비교·에러 조사 필요 시 |

---

## 에이전트 추가 방법

1. `.claude/agents/{name}.md` 파일 생성
2. YAML frontmatter에 `name`, `description` (필수), `model`, `color` (선택) 포함
3. `description`은 구체적으로 — Claude가 이걸 읽고 자동 호출 여부를 결정
4. 이 `README.md`의 목록 업데이트
