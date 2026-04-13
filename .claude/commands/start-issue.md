---
description: Ready 이슈 작업을 시작한다. 워크트리, 브랜치, work 폴더를 생성하고 이슈를 assign한다. 사용법: /start-issue <이슈번호|짧은이름> [짧은이름]
---

## 인수 형식 (유연하게 지원)

`$ARGUMENTS`는 다음 형식 중 하나다:

| 형식 | 예시 | 동작 |
|------|------|------|
| 이슈번호만 | `15` | 이슈 조회 → 제목에서 type·짧은이름 자동 추출 |
| 이슈번호 + 짧은이름 | `15 my-feature` | type은 이슈 제목에서 추출 |
| 6자리 패딩 + 짧은이름 | `000015-my-feature` | 이슈번호·짧은이름 분리 후 type 조회 |
| 제목 문자열 | `기능명 키워드` | `gh issue list --search`로 이슈 검색 |

## 실행 순서

### 1. 인수 파싱 및 이슈 조회

**이슈번호(정수)만 있는 경우:**
```
gh issue view {번호} --json number,title,labels
```
제목에서 type 추출 규칙:
- `[FEAT]` 또는 `feat:` 접두사 → `feat`
- `[FIX]` 또는 `fix:` → `fix`
- `[CHORE]` 또는 `chore:` → `chore` (→ `refactor` 사용)
- `[DOCS]` 또는 `docs:` → `docs`
- 접두사 없으면 labels에서 추출, 없으면 `feat` 기본값

짧은이름 자동 생성 규칙 (짧은이름 미제공 시):
- 제목에서 접두사(`[...]`, `feat:` 등) 제거
- 영문+숫자만 남기고 소문자 변환, 공백→하이픈
- 3단어 이내로 자름

**`000015-my-feature` 형식인 경우:**
- 앞 6자리 숫자 = 이슈번호, 나머지 = 짧은이름
- `gh issue view`로 type 조회

**제목 문자열인 경우:**
```
gh issue list --search "{제목}" --json number,title
```
첫 번째 결과를 사용. 여러 개이면 목록 출력 후 선택 요청.

### 2. 이슈 상태 검증

이슈가 CLOSED 상태이면 즉시 중단한다.
프로젝트 보드 상태가 "Ready"가 아니면 경고를 출력하고 사용자 확인을 받는다.

### 3. 이름 확정

- PADDED = 이슈번호 6자리 zero-pad (15 → `000015`)
- WORKTREE = `.worktree/{PADDED}-{짧은이름}`
- BRANCH = `{type}/{PADDED}-{짧은이름}`
- WORKFOLDER = `docs/work/active/{PADDED}-{짧은이름}`
- PORT = `3000 + {이슈번호}` (예: 이슈 #116 → 포트 3116)

확정된 값을 보여주고 진행한다.

### 4. 중복 확인

`git worktree list`를 실행해서 WORKTREE 경로가 이미 있는지 확인한다.
이미 존재하면 오류 안내 후 중단.

### 5. main 최신화

```
git pull origin main
```

### 6. Worktree + 브랜치 생성

```
git worktree add {WORKTREE} -b {BRANCH}
```

### 6-1. gitignore 대상 심볼릭 링크 생성

메인 워크트리에 있는 gitignore 대상 파일/디렉토리를 새 워크트리에 심볼릭 링크로 연결한다.

링크 대상 (존재하는 것만):
- `.env`, `.env.*`
- `tests/fixtures`
- `.venv`
- `node_modules`

### 7. Work 폴더 생성

```
mkdir -p {WORKTREE}/{WORKFOLDER}
```

#### 7-1. `00_issue.md` 생성

```
gh issue view {이슈번호} --json title,body \
  --jq '"# " + .title + "\n\n" + .body' \
  > {WORKTREE}/{WORKFOLDER}/00_issue.md
```

하단에 `## 작업 내역` 섹션 추가.

#### 7-2. `01_plan.md` 생성 (AC 체크리스트 포함)

이슈 body의 AC 섹션(`## AC`, `## 완료 기준`, `## Acceptance Criteria`)을 추출해
체크리스트로 변환한 `01_plan.md` 초안 생성.

> ⚠️ 이 단계에서는 AC 체크리스트만 포함된 **초안**이 생성된다.
> 구현 시작 전 반드시 `/plan` 커맨드를 실행하여 구체적 구현 계획을 작성해야 한다.
> `/remind-issue` 실행 시 플랜 품질이 자동 검증되며, 미흡하면 구현이 차단된다.

### 8. 이슈 Assign

```
gh issue edit {이슈번호} --add-assignee @me
```

### 9. 완료 안내

```
✓ 워크트리: {WORKTREE}
✓ 브랜치:   {BRANCH}
✓ 폴더:     {WORKFOLDER}
✓ 이슈 #{이슈번호} assign 완료

다음 명령으로 작업 디렉토리로 이동하세요:
  cd {WORKTREE}

dev 서버 실행 시 이 워크트리 전용 포트를 사용하세요 (포트 충돌 방지):
  npm run dev -- --port {PORT}
```

작업 시작 전 체크리스트 안내:
1. 작업 대상 디렉토리의 `.ai.md` 읽기
2. `docs/specs/`에서 AC 확인
3. 불변식 확인 (`CLAUDE.md`)
