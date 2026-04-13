#!/bin/bash
# setup.sh — 템플릿 초기화 스크립트
# 새 프로젝트에서 1회 실행. 플레이스홀더를 실제 값으로 치환합니다.

set -e

echo "================================================"
echo "  siw-claude-template 초기화"
echo "================================================"
echo ""

# 1. 프로젝트 이름 입력
read -p "프로젝트 이름 (예: my-awesome-project): " PROJECT_NAME
if [ -z "$PROJECT_NAME" ]; then
  echo "❌ 프로젝트 이름은 필수입니다."
  exit 1
fi

# 2. 플레이스홀더 치환
echo ""
echo "📝 플레이스홀더 치환 중..."

find . -name "*.md" -not -path "./.git/*" -not -path "./node_modules/*" | while read f; do
  sed -i "s/{{PROJECT_NAME}}/$PROJECT_NAME/g" "$f"
done

echo "  ✓ CLAUDE.md, AGENTS.md, docs/ 내 {{PROJECT_NAME}} 치환 완료"

# 3. update-changelog 스코프 안내
echo ""
echo "📋 update-changelog 스코프 설정"
echo "  .claude/commands/update-changelog.md 에서 SCOPE → 경로 매핑을 직접 수정하세요."
echo "  (현재: root / src 두 가지 예시가 포함되어 있습니다)"

# 4. 불변식 설정 안내
echo ""
echo "🔒 불변식 설정"
echo "  CLAUDE.md 의 '아키텍처 불변식' 섹션을 프로젝트에 맞게 수정하세요."
echo "  scripts/check_invariants.py 에 해당 불변식 검사 로직을 작성하세요."

# 5. GitHub Project 보드 자동 생성
echo ""
echo "📌 GitHub Project 보드 설정"

# gh CLI 로그인 확인
if ! gh auth status &>/dev/null; then
  echo "  ⚠️  GitHub CLI 로그인이 필요합니다. 'gh auth login' 실행 후 다시 시도하세요."
  echo "  → 수동 설정 방법: docs/onboarding/getting-started.md Step 5"
else
  read -p "  GitHub Project 보드를 자동으로 생성할까요? (y/n): " CREATE_PROJECT
  if [ "$CREATE_PROJECT" = "y" ] || [ "$CREATE_PROJECT" = "Y" ]; then
    # 레포 owner 조회
    OWNER=$(gh repo view --json owner -q '.owner.login' 2>/dev/null)
    if [ -z "$OWNER" ]; then
      echo "  ❌ 레포 정보를 가져올 수 없습니다. remote가 설정되어 있는지 확인하세요."
    else
      echo "  🔧 프로젝트 보드 생성 중..."

      # 프로젝트 생성
      PROJECT_URL=$(gh project create --owner "$OWNER" --title "$PROJECT_NAME" --format json 2>/dev/null | jq -r '.url // empty')

      if [ -z "$PROJECT_URL" ]; then
        echo "  ❌ 프로젝트 생성 실패. 권한을 확인하세요."
        echo "  → 수동 설정 방법: docs/onboarding/getting-started.md Step 5"
      else
        # URL에서 프로젝트 번호 추출 (예: .../projects/3 → 3)
        PROJECT_NUMBER=$(echo "$PROJECT_URL" | grep -oE '[0-9]+$')
        echo "  ✓ 프로젝트 보드 생성 완료 (번호: $PROJECT_NUMBER)"

        # 레포에 프로젝트 보드 연결
        REPO_NAME=$(gh repo view --json nameWithOwner -q '.nameWithOwner' 2>/dev/null)
        if [ -n "$REPO_NAME" ]; then
          gh project link "$PROJECT_NUMBER" --owner "$OWNER" --repo "$REPO_NAME" 2>/dev/null
          echo "  ✓ 레포에 프로젝트 보드 연결 완료"
        fi

        # Project ID 조회
        echo "  🔍 프로젝트 ID 조회 중..."
        PROJECT_ID=$(gh project view "$PROJECT_NUMBER" --owner "$OWNER" --format json 2>/dev/null | jq -r '.id // empty')

        if [ -z "$PROJECT_ID" ]; then
          echo "  ❌ 프로젝트 ID를 조회할 수 없습니다."
          echo "  → 수동 설정 방법: docs/onboarding/getting-started.md Step 5"
        else
          # Status 필드 ID 조회
          FIELDS_JSON=$(gh project field-list "$PROJECT_NUMBER" --owner "$OWNER" --format json 2>/dev/null)
          FIELD_ID=$(echo "$FIELDS_JSON" | jq -r '.fields[] | select(.name == "Status") | .id // empty')

          if [ -z "$FIELD_ID" ]; then
            echo "  ❌ Status 필드를 찾을 수 없습니다."
            echo "  → 수동 설정 방법: docs/onboarding/getting-started.md Step 5"
          else
            # GraphQL로 Status 필드 옵션을 원하는 컬럼으로 교체
            echo "  🔧 Status 컬럼 설정 중 (Backlog/Ready/In Progress/In Review/Done)..."
            UPDATED_FIELD=$(gh api graphql -f query='
              mutation($fieldId: ID!) {
                updateProjectV2Field(input: {
                  fieldId: $fieldId
                  name: "Status"
                  singleSelectOptions: [
                    {name: "Backlog", color: GRAY, description: "새로 생성된 이슈"},
                    {name: "Ready", color: BLUE, description: "작업 준비 완료"},
                    {name: "In Progress", color: YELLOW, description: "작업 진행 중"},
                    {name: "In Review", color: ORANGE, description: "PR 리뷰 중"},
                    {name: "Done", color: GREEN, description: "완료"}
                  ]
                }) {
                  projectV2Field {
                    ... on ProjectV2SingleSelectField {
                      id
                      options {
                        id
                        name
                      }
                    }
                  }
                }
              }
            ' -f fieldId="$FIELD_ID" 2>/dev/null)

            if [ -z "$UPDATED_FIELD" ]; then
              echo "  ⚠️  Status 컬럼 자동 설정 실패. GraphQL 권한을 확인하세요."
              echo "  → GitHub 웹에서 수동으로 컬럼을 추가하세요."
            else
              echo "  ✓ Status 컬럼 설정 완료"

              # 업데이트된 옵션 ID 추출
              OPTION_BACKLOG=$(echo "$UPDATED_FIELD" | jq -r '.data.updateProjectV2Field.projectV2Field.options[] | select(.name == "Backlog") | .id')
              OPTION_IN_PROGRESS=$(echo "$UPDATED_FIELD" | jq -r '.data.updateProjectV2Field.projectV2Field.options[] | select(.name == "In Progress") | .id')
              OPTION_IN_REVIEW=$(echo "$UPDATED_FIELD" | jq -r '.data.updateProjectV2Field.projectV2Field.options[] | select(.name == "In Review") | .id')
            fi

            # workflow 파일에 자동 치환 (값이 있는 것만)
            WF_FILE=".github/workflows/project-automation.yml"
            if [ -f "$WF_FILE" ]; then
              [ -n "$PROJECT_ID" ] && sed -i "s|YOUR_PROJECT_ID|$PROJECT_ID|g" "$WF_FILE"
              [ -n "$FIELD_ID" ] && sed -i "s|YOUR_STATUS_FIELD_ID|$FIELD_ID|g" "$WF_FILE"
              [ -n "$OPTION_BACKLOG" ] && sed -i "s|YOUR_BACKLOG_OPTION_ID|$OPTION_BACKLOG|g" "$WF_FILE"
              [ -n "$OPTION_IN_PROGRESS" ] && sed -i "s|YOUR_IN_PROGRESS_OPTION_ID|$OPTION_IN_PROGRESS|g" "$WF_FILE"
              [ -n "$OPTION_IN_REVIEW" ] && sed -i "s|YOUR_IN_REVIEW_OPTION_ID|$OPTION_IN_REVIEW|g" "$WF_FILE"
              sed -i "s|PROJECT_NUMBER=\"1\"|PROJECT_NUMBER=\"$PROJECT_NUMBER\"|g" "$WF_FILE"
              echo "  ✓ project-automation.yml ID 자동 입력 완료"
            fi

            echo ""
            echo "  📋 조회된 값:"
            echo "    PROJECT_NUMBER: $PROJECT_NUMBER"
            echo "    PROJECT_ID:     $PROJECT_ID"
            echo "    FIELD_ID:       $FIELD_ID"
            [ -n "$OPTION_BACKLOG" ] && echo "    OPTION_BACKLOG:       $OPTION_BACKLOG"
            [ -n "$OPTION_IN_PROGRESS" ] && echo "    OPTION_IN_PROGRESS:   $OPTION_IN_PROGRESS"
            [ -n "$OPTION_IN_REVIEW" ] && echo "    OPTION_IN_REVIEW:     $OPTION_IN_REVIEW"
          fi
        fi

        echo ""
        echo "  🔑 PROJECT_TOKEN Secret 등록 필요 (수동)"
        echo "    1. GitHub → Settings → Developer settings → Personal access tokens"
        echo "    2. Fine-grained token 생성 (Projects: Read and write)"
        echo "    3. 레포 Settings → Secrets → Actions → 'PROJECT_TOKEN' 으로 등록"
      fi
    fi
  else
    echo "  → 수동 설정 방법: docs/onboarding/getting-started.md Step 5"
  fi
fi

# 6. hooks 실행 권한
echo ""
echo "🔐 훅 실행 권한 부여..."
chmod +x .claude/hooks/secret-filter.sh 2>/dev/null || true
echo "  ✓ .claude/hooks/secret-filter.sh 실행 권한 설정 완료"

# 7. .gitkeep 삭제 안내
echo ""
echo "📁 초기 디렉토리"
echo "  docs/work/active/, docs/work/done/, docs/specs/ 에 .gitkeep 파일이 있습니다."
echo "  첫 파일 추가 후 삭제하세요."

echo ""
echo "================================================"
echo "  ✅ '$PROJECT_NAME' 초기화 완료!"
echo ""
echo "  다음 단계:"
echo "  1. docs/onboarding/getting-started.md 를 읽으세요"
echo "  2. GitHub Project 보드를 생성하고 연결하세요"
echo "  3. CLAUDE.md 불변식을 프로젝트에 맞게 수정하세요"
echo "  4. AGENTS.md 레포 구조를 실제 구조로 업데이트하세요"
echo "================================================"
