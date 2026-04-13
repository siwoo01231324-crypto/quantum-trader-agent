---
description: scope별 CHANGELOG.md를 최신 git 로그 기준으로 업데이트한다. 사용법: /update-changelog [scope] [since:<날짜|태그>]
---

당신은 프로젝트의 `/update-changelog` 커맨드를 실행하고 있습니다.

## 인수 파싱

`$ARGUMENTS`에서 다음을 추출한다:
- **scope** (선택): 아래 Scope → 경로 매핑 참조. 생략 시 모든 scope를 처리한다.
- **since** (선택): `since:2026-01-01` 또는 `since:v1.0.0` 형식.
  생략 시 각 CHANGELOG.md의 가장 최근 주차 날짜 이후 커밋을 자동 감지한다.

## Scope → 경로 매핑

> ⚠️ 프로젝트 구조에 맞게 수정하세요.

| scope | git log 경로 필터 | CHANGELOG 경로 |
|-------|-------------------|----------------|
| root  | `. -- ':!src'` (인프라·문서) | `CHANGELOG.md` |
| src   | `src/` | `src/CHANGELOG.md` |

## 실행 단계

### Step 1. 기준 날짜 결정

각 scope의 CHANGELOG.md를 읽어 가장 최근 `## YYYY년 M월 D일 주차` 헤더 날짜를 파악한다.
CHANGELOG.md가 없거나 비어있으면 `--since="30 days ago"`를 기본값으로 사용한다.

### Step 2. git log 수집

```bash
git log --no-merges \
  --format="%ad %H %s" \
  --date=format:"%Y-%m-%d" \
  --since="<기준날짜>" \
  -- <scope 경로>
```

다음 노이즈 커밋은 제외한다:
- `chore: docs/work 폴더 초기화`
- `chore: docs/work done 이동`
- `chore: docs/work active → done`

### Step 3. 커밋 분류 및 사용자 친화적 변환

| 커밋 prefix | 카테고리 |
|-------------|----------|
| `feat:` | ✨ 새 기능 |
| `fix:` | 🐛 버그 수정 |
| `refactor:` | 🔧 개선 |
| `chore:` | 🔧 개선 |
| `docs:` | 📚 문서 |
| `perf:` | ⚡ 성능 |

변환 규칙:
- 이슈 번호 `(#N)` → `([#N](../../issues/N))` GitHub 링크로 변환
- 관련 커밋 여러 개가 같은 기능이면 하나의 항목으로 합친다
- 내부 리팩토링·테스트 픽스처 커밋은 상위 기능 항목에 포함

### Step 4. 주차 그룹핑

커밋을 월요일 기준 ISO 주차로 묶는다.
헤더 형식: `## YYYY년 M월 D일 주차 (Mon D~)`

### Step 5. CHANGELOG.md 업데이트

새로운 주차 섹션을 기존 CHANGELOG.md 첫 번째 `---` 구분선 바로 아래에 prepend한다.
같은 주차 헤더가 있으면 해당 섹션에 항목을 병합한다 (중복 제거).

### Step 6. 결과 요약 출력

```
✅ CHANGELOG.md — 5개 커밋 → 3항목 추가 (2026년 1월 2일 주차)
⏭️  src/CHANGELOG.md — 변경 없음 (해당 기간 커밋 없음)
```

## 사용 예시

```
/update-changelog                    # 모든 scope 업데이트
/update-changelog src                # src만 업데이트
/update-changelog root since:2026-01-01
/update-changelog src since:v1.0.0
```
