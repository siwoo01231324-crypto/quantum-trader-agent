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
