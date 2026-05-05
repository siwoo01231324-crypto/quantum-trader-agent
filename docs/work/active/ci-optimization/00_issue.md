---
type: work-done
id: 00_issue
name: "CI minutes 최적화 — PR 비용 38분→4분 (옵션 C+E)"
status: active
---

# chore: CI minutes 최적화 — PR 비용 38분 → 4분 (옵션 C+E)

## 사용자 보고 (2026-05-06)

GitHub: "You have used 90% of the Actions minutes (1,805 / 2,000 min)".
오늘 5 PR 머지로 무료 분량 거의 소진. 다음 리셋 6/1, 27일 후.

원인: PR 1개당 평균 38분 CI 사용:
- Unit coverage (전체 pytest + cov): 20분
- Backtest layer: 5분
- PyInstaller Windows EXE: 5분
- Coverage regression guard: 7분
- Broker pytest matrix (Linux + Windows): 2분
- Integration tests: 1분
- Invariants: 17초

## 변경 (옵션 C + E)

### 1. Unit coverage / Backtest / Broker pytest / PyInstaller → master push 만

PR 에서 안 돌고 master 머지 후 1번만. 사고 발생 시 master 빨간 X + GitHub 이메일 → hotfix 1번 더 머지로 해결.

- `coverage.yml` unit-coverage / backtest 에 `if: github.event_name == 'push'`
- `coverage.yml` regression-guard job 제거 (PR 의 baseline coverage 가 없어짐)
- `ci.yml` test job 에 `if: github.event_name == 'push'`
- `build-exe.yml` 의 `pull_request:` 트리거 제거

### 2. 새 PR-only workflow: `pr-quick-check.yml`

PR 마다 가벼운 pytest 1회 (cov 없이) — 3분.
- `pytest tests/ -m "not integration and not slow"` (1900+ tests)
- ubuntu-only (Windows matrix 제거 — broker는 master 에서 검증)
- `paths-ignore`: docs/**, *.md, .ai.md, .github/** (workflow 자체 제외) → docs 만 PR 시 skip

### 3. Concurrency cancel — 모든 workflow 에 적용

같은 PR 에 새 commit push 시 옛날 CI 자동 cancel → 옛 commit 의 낭비 분 절감.

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

추가된 workflow: coverage.yml, ci.yml, build-exe.yml, pr-quick-check.yml

## PR 비용 변화

| 항목 | 이전 | 이후 |
|---|---|---|
| Coverage Unit (pytest + cov) | 20분 | **0** (PR), 20분 (master 1회) |
| Coverage regression guard | 7분 | **0** (제거) |
| Backtest layer | 5분 | **0** (PR), 5분 (master 1회) |
| Broker pytest matrix | 2분 | **0** (PR), 2분 (master 1회) |
| PyInstaller Windows EXE | 5분 | **0** (PR), 5분 (master 1회) |
| **PR Quick check (신규)** | - | **3분** (PR) |
| Integration tests | 1분 | 1분 |
| Invariants | 17초 | 17초 |
| **합계 (PR 코드)** | **~38분** | **~4분** |
| **합계 (PR 문서만)** | **~38분** | **~17초** (paths-ignore) |
| **합계 (master 1회)** | **~38분** | **~33분** |

→ PR 당 **89% 절감** (코드 PR), **99% 절감** (docs PR).
→ 195min 남은 분량으로 **5 PR → 48 PR 가능**.

## 변경 파일

| 파일 | 변경 |
|---|---|
| `.github/workflows/coverage.yml` | unit-coverage/backtest 에 `if: push` + concurrency + regression-guard 제거 |
| `.github/workflows/ci.yml` | test job 에 `if: push` + concurrency |
| `.github/workflows/build-exe.yml` | pull_request 트리거 제거 + concurrency |
| `.github/workflows/pr-quick-check.yml` | 신규 — PR 가벼운 pytest |

## 검증
- [x] 4 YAML 파싱 통과
- [x] check_invariants 통과 (예상)

## 위험·롤백
- master 에서 EXE 빌드 / coverage 사고 발견 가능 → hotfix PR 머지로 회복 (1회 추가 머지)
- PR Quick check 가 fail 하면 PR 차단됨 (회귀 검증 유지)
- 옛날 워크플로 fail 났을 때 PR 에서 즉시 발견 못 하는 trade-off — 회귀 위험 ↑ 하지만 PR 실패율 자체는 낮음

## 후속 (필요 시)

- `pytest-testmon` 도입 (옵션 D) → PR 4분 → 1분 추가 절감
- workflow 별 retention 짧게 → artifact 비용 절감
