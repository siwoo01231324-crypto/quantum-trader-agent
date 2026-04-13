---
description: 이슈를 중도 포기한다. 사유를 입력받고 이슈를 닫은 뒤 워크트리·브랜치를 정리한다. 사용법: /drop-issue [이슈번호|짧은이름]
---

## 인수 형식 (모두 선택사항)

`$ARGUMENTS`는 다음 형식 중 하나거나 생략 가능하다:

| 형식 | 예시 | 동작 |
|------|------|------|
| 생략 | (없음) | 현재 브랜치에서 자동 감지 |
| 이슈번호만 | `15` | 이슈번호로 브랜치·워크트리 탐색 |
| 6자리 패딩+짧은이름 | `000015-my-feature` | 직접 브랜치명 구성 |
| 짧은이름만 | `my-feature` | 브랜치 목록에서 매칭 |

## 실행 순서

### 1. 브랜치·이슈번호 확정

```
git branch --show-current
```
또는 인수에서 파싱.

브랜치가 없으면 이슈 번호만으로 진행 (브랜치·워크트리 미시작 이슈).

### 2. CWD 감지

현재 경로가 대상 워크트리 내부이면 메인 워크트리로 이동 후 재실행하도록 안내하고 중단.

### 3. 미커밋 변경사항 확인

```
git -C {WORKTREE} status --short
```

미저장 변경사항이 있으면 경고 출력 후 사용자 확인.

### 4. 포기 사유 입력

포기 사유를 입력받는다 (빈 값 허용 안 함).

### 5. GitHub 이슈 코멘트

```
gh issue comment {이슈번호} --body "## 이슈 포기

**사유:** {입력된 사유}
**날짜:** {오늘 날짜}"
```

### 5.5. 프로젝트 보드 → Drop 이동

이슈를 프로젝트 보드에서 "Drop" 상태로 이동한다:

```bash
# cleanup-issue.md의 3.5단계와 동일한 GraphQL 쿼리 사용
# 1. 이슈의 프로젝트 아이템 ID + PROJECT_ID 조회
# 2. Status 필드에서 "Drop" 옵션 ID 조회
# 3. updateProjectV2ItemFieldValue mutation 실행
```

실패 시 경고만 출력하고 계속 진행한다.

### 6. 이슈 Close

```
gh issue close {이슈번호} --reason "not planned"
```

### 7. 작업 폴더 이동

`{WORKFOLDER}`가 존재하면 포기 정보를 `00_issue.md`에 추가하고:
```
mv {WORKFOLDER} docs/work/done/DROPPED-{PADDED}-{짧은이름}
```

### 8~10. 워크트리·로컬·리모트 브랜치 삭제

```
git worktree remove --force {WORKTREE}
git branch -D {브랜치명}
git push origin --delete {브랜치명}
```

### 11. 완료 안내

```
✓ 이슈 #{이슈번호} 포기 처리 완료
- 이슈 상태: CLOSED (not planned)
- 작업 폴더: docs/work/done/DROPPED-{PADDED}-{짧은이름}
- 워크트리·브랜치: 삭제됨
```
