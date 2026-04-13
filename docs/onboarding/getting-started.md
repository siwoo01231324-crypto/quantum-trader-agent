# Getting Started — 처음 시작 가이드

> 이 템플릿을 새 프로젝트에 적용할 때 **처음 한 번** 읽는 문서입니다.
> 순서대로 따라하면 전체 워크플로우가 동작하는 상태가 됩니다.

---

## 전제 조건

시작 전 아래가 모두 설치되어 있는지 확인하세요.

| 도구 | 확인 명령 | 용도 |
|------|-----------|------|
| Git | `git --version` | 버전 관리 |
| GitHub CLI | `gh --version` | 이슈·PR·Projects 자동화 |
| Claude Code | `claude --version` | AI 코딩 어시스턴트 |
| Python 3 | `python3 --version` | 불변식 검사 스크립트 |

GitHub CLI 로그인:
```bash
gh auth login
```

---

## Step 1. 레포 생성

### 옵션 A — GitHub 템플릿에서 생성 (권장)
```bash
gh repo create my-project \
  --private \
  --template siw/siw-claude-template \
  --clone

cd my-project
```

### 옵션 B — 직접 클론 후 remote 변경
```bash
git clone https://github.com/siw/siw-claude-template my-project
cd my-project
git remote set-url origin https://github.com/YOUR_USERNAME/my-project
```

---

## Step 2. 초기화 스크립트 실행

```bash
bash setup.sh
```

스크립트가 안내하는 대로:
1. **프로젝트 이름** 입력 → `CLAUDE.md`, `AGENTS.md` 내 `{{PROJECT_NAME}}` 자동 치환
2. 이후 수동 설정 항목 안내 출력

---

## Step 3. CLAUDE.md 불변식 정의

`CLAUDE.md`의 `## 아키텍처 불변식` 섹션을 프로젝트에 맞게 수정하세요.

```markdown
## 아키텍처 불변식 (위반 시 CI 차단)
1. 예: DB 접근은 repository 레이어에서만
2. 예: 외부 API 호출은 service 레이어에서만
3. 예: 테스트 없는 PR은 머지 금지
```

그 다음 `scripts/check_invariants.py`에 해당 불변식을 검사하는 로직을 작성하세요.
파일 내 주석에 예시 패턴이 포함되어 있습니다.

---

## Step 4. AGENTS.md 레포 구조 업데이트

`AGENTS.md`의 레포 구조 트리를 실제 프로젝트 구조로 수정하세요.

```markdown
## 레포 구조
my-project/
├── src/
│   ├── api/       API 라우터
│   ├── services/  비즈니스 로직
│   └── db/        데이터베이스
└── tests/
```

---

## Step 5. GitHub Project 보드 생성 및 연결

칸반 자동화(`project-automation.yml`)가 동작하려면 GitHub Projects 보드와 연결이 필요합니다.

### 5-1. 자동 설정 (권장)

`setup.sh` 실행 시 "GitHub Project 보드를 자동으로 생성할까요?" 프롬프트에서 `y`를 선택하면:
- 프로젝트 보드 생성
- 필드 ID 자동 조회
- `project-automation.yml`에 ID 자동 입력

까지 한 번에 처리됩니다.

**자동 설정 후 수동으로 해야 할 것:**

1. **PROJECT_TOKEN Secret 등록** — 아래 5-4 참고

### 5-2. 수동 설정

`setup.sh`에서 자동 설정을 건너뛴 경우 수동으로 진행합니다.

**보드 생성:**
```bash
# gh CLI로 생성
gh project create --owner YOUR_USERNAME --title "프로젝트명"

# 또는 GitHub 웹 UI에서:
# 레포 → Projects 탭 → New project → Board 템플릿 선택
```
컬럼 생성: `Backlog` / `Ready` / `In Progress` / `In Review` / `Done`

### 5-3. 필요한 ID 조회 (수동 설정 시)

```bash
# Project 목록 및 번호 확인
gh project list --owner YOUR_USERNAME

# Project 필드 목록 (field-id, option-id 확인)
gh project field-list 1 --owner YOUR_USERNAME --format json | jq .
```

출력에서 다음을 찾아 메모하세요:
- `id` (PVT_xxx) → `PROJECT_ID`
- Status 필드의 `id` (PVTSSF_xxx) → `FIELD_ID`
- 각 상태 옵션의 `id` → `OPTION_BACKLOG`, `OPTION_IN_PROGRESS`, `OPTION_IN_REVIEW`

`.github/workflows/project-automation.yml`에서 `⚠️ 설정 필요` 주석이 달린 변수들을 채웁니다:

```yaml
PROJECT_NUMBER: "1"           # gh project list 에서 확인한 번호
PROJECT_ID: "PVT_xxx"         # field-list 출력의 id
FIELD_ID: "PVTSSF_xxx"        # Status 필드 id
OPTION_BACKLOG: "xxx"          # Backlog 옵션 id
OPTION_IN_PROGRESS: "xxx"      # In Progress 옵션 id
OPTION_IN_REVIEW: "xxx"        # In Review 옵션 id
```

### 5-4. PROJECT_TOKEN Secret 등록

GitHub Actions가 Projects API를 호출하려면 `project` 스코프가 있는 PAT가 필요합니다.

1. GitHub → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens**
2. **Repository access**: 이 레포 선택
3. **Permissions**: `Projects` → Read and write
4. 토큰 생성 후 복사

레포 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
- Name: `PROJECT_TOKEN`
- Value: 복사한 토큰

---

## Step 6. update-changelog 스코프 설정

`.claude/commands/update-changelog.md`의 Scope → 경로 매핑을 프로젝트 구조에 맞게 수정하세요.

```markdown
| scope | git log 경로 필터 | CHANGELOG 경로 |
|-------|-------------------|----------------|
| root  | `. -- ':!src'`    | `CHANGELOG.md` |
| src   | `src/`            | `src/CHANGELOG.md` |
```

---

## Step 7. 각 디렉토리에 .ai.md 작성

레포 규칙: **모든 디렉토리에 `.ai.md` 포함**.

새 디렉토리를 만들 때마다 `.ai.md`를 함께 작성하세요.
`documentation-architect` 에이전트를 활용하면 자동으로 작성됩니다:

```
"이 디렉토리에 .ai.md를 작성해줘"
```

---

## Step 8. 첫 이슈 생성 테스트

모든 설정이 완료되면 첫 이슈를 생성해 워크플로우를 검증합니다:

```
/bi feat "첫 기능 이슈 테스트" "템플릿 워크플로우 검증용"
```

이슈가 GitHub Project 보드의 **Backlog**에 자동으로 나타나면 설정 완료입니다.

---

## 일상 워크플로우 요약

```
1. /bi     → 이슈 생성 (Backlog)
2. /si N   → 이슈 시작 (워크트리 + 브랜치 생성, In Progress)
3. /ri     → 세션 재시작 시 현황 복구
4. /plan   → 구현 계획 작성
5. /fi     → 완료 커밋 + PR 생성 (In Review)
6. /ci     → PR 머지 후 정리
```

---

## 커맨드 상세 레퍼런스

| 커맨드 | 전체 이름 | 위치 |
|--------|-----------|------|
| `/bi` | `/backlog-issue` | `.claude/commands/backlog-issue.md` |
| `/si` | `/start-issue` | `.claude/commands/start-issue.md` |
| `/ri` | `/remind-issue` | `.claude/commands/remind-issue.md` |
| `/plan` | `/plan` | `.claude/commands/plan.md` |
| `/fi` | `/finish-issue` | `.claude/commands/finish-issue.md` |
| `/ci` | `/cleanup-issue` | `.claude/commands/cleanup-issue.md` |
| `/drop-issue` | `/drop-issue` | `.claude/commands/drop-issue.md` |
| `/update-changelog` | `/update-changelog` | `.claude/commands/update-changelog.md` |

---

## 트러블슈팅

### 이슈가 Project 보드에 안 나타나는 경우
- `PROJECT_TOKEN` Secret이 등록됐는지 확인
- 토큰에 `project` 스코프가 있는지 확인
- `.github/workflows/project-automation.yml`의 ID 값들이 올바른지 확인
- Actions 탭에서 워크플로우 실행 로그 확인

### `/si` 실행 시 워크트리 생성 실패
- `git worktree list` 로 현재 워크트리 목록 확인
- 동일한 이름의 워크트리가 이미 있으면 `/ci` 또는 `git worktree remove` 로 정리

### secret-filter.sh 권한 오류
```bash
chmod +x .claude/hooks/secret-filter.sh
```
