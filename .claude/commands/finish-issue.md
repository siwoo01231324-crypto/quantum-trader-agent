---
description: 구현 완료 후 커밋하고 PR을 생성한다. Claude가 변경사항을 분석해 커밋 메시지를 자동 생성한다. 사용법: /finish-issue [이슈번호|짧은이름]
---

## 인수 형식 (모두 선택사항)

`$ARGUMENTS`는 다음 형식 중 하나거나 생략 가능하다:

| 형식 | 예시 | 동작 |
|------|------|------|
| 생략 | (없음) | 현재 브랜치에서 자동 감지 |
| 이슈번호만 | `15` | 이슈번호로 브랜치 탐색 |
| 6자리 패딩+짧은이름 | `000015-my-feature` | 직접 브랜치명 구성 |
| 짧은이름만 | `my-feature` | 브랜치 목록에서 매칭 |

## 실행 순서

### 1. 브랜치·이슈번호 확정

**인수 없는 경우 (워크트리 내 실행):**
```
git branch --show-current
```
결과 예: `feat/000015-my-feature` → type=`feat`, PADDED=`000015`, 짧은이름=`my-feature`, 이슈번호=`15`

### 2. 변경사항 확인

```
git status
git diff --stat
```

### 3. 커밋 메시지 자동 생성

`git diff HEAD` 로 변경 내용을 분석한다.
형식: `{type}: {변경 내용 요약} (#{이슈번호})`

### 4. 코드 간소화

커밋 전, `simplify` 스킬을 호출해 변경된 파일을 검토하고 정리한다:
```
/simplify
```

### 5. 코드 리뷰

변경사항 전체를 검토한다:
- **버그·로직 오류**: 잘못된 조건, 경계값, 예외 처리 누락
- **불변식 위반**: `CLAUDE.md` 기재 불변식
- **테스트 누락**: 새 코드에 대응하는 테스트가 없는 경우
- **코드 품질**: 중복 코드, 불명확한 변수명

문제 발견 시 출력하고 수정 여부를 묻는다.

### 6. AC 체크

`{WORKFOLDER}/00_issue.md` 또는 이슈 본문에서 완료 기준을 추출해 충족 여부를 판단한다.
충족된 항목은 `- [x]`로 업데이트. 미충족 항목이 있으면 계속 여부를 묻는다.

### 7. .ai.md 업데이트 확인

작업 대상 디렉토리의 `.ai.md`가 최신 상태인지 확인한다.

### 8. 00_issue.md 작업 내역 작성

변경된 파일·이슈 배경을 분석해 `## 작업 내역` 섹션에 기록한다.

### 9. docs/work active → done 이동

```
mv {WORKFOLDER} docs/work/done/{PADDED}-{짧은이름}
```

### 10. 사용자 확인 및 커밋

커밋 메시지와 변경 파일 내역을 보여주고 확인을 받는다.
금지 파일(`.pdf`, `.csv`, `.pkl`, `.parquet`, `test-results/`, `playwright-report/`) 감지 시 경고.

```
git add .
git commit -m "{커밋 메시지}"
```

### 11. Push

```
git push origin {BRANCH}
```

### 12. PR 생성

```
gh pr create \
  --title "{커밋 메시지}" \
  --body "## 이슈 배경
{00_issue.md 내용 요약}

## 완료 기준 (AC)
{AC 체크 결과}

## 작업 내역
{00_issue.md의 ## 작업 내역 섹션}

Closes #{이슈번호}"
```

### 13. 프로젝트 보드 → In Review 이동

PR 생성 후 이슈를 프로젝트 보드에서 "In Review" 상태로 이동한다:

```bash
# 1. 이슈의 프로젝트 아이템 ID 조회
ITEM_ID=$(gh api graphql -f query='
  query($owner:String!, $repo:String!, $number:Int!) {
    repository(owner:$owner, name:$repo) {
      issue(number:$number) {
        projectItems(first:10) { nodes { id project { id title } } }
      }
    }
  }' -f owner="{OWNER}" -f repo="{REPO}" -F number={이슈번호} \
  --jq '.data.repository.issue.projectItems.nodes[0].id')

PROJECT_ID=$(# 위 쿼리에서 project.id 추출)

# 2. Status 필드에서 "In Review" 옵션 ID 조회
FIELD_ID=$(# Status 필드 ID)
IN_REVIEW_ID=$(# options[] | select(.name=="In Review") | .id)

# 3. In Review로 이동
gh api graphql -f query='
  mutation($projectId:ID!, $itemId:ID!, $fieldId:ID!, $optionId:String!) {
    updateProjectV2ItemFieldValue(input:{
      projectId:$projectId, itemId:$itemId, fieldId:$fieldId,
      value:{singleSelectOptionId:$optionId}
    }) { projectV2Item { id } }
  }' -f projectId="$PROJECT_ID" -f itemId="$ITEM_ID" \
     -f fieldId="$FIELD_ID" -f optionId="$IN_REVIEW_ID"
```

실패 시 경고만 출력하고 계속 진행한다.

### 14. 완료 안내

```
✓ PR 생성 완료: {PR URL}
PR 머지 후 /cleanup-issue {이슈번호} 을 실행하세요.
```
