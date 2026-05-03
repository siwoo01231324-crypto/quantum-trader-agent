---
type: work-active
id: 000132-test-coverage
issue: 132
title: "테스트 커버리지 지표 + 3계층 전략"
status: in_progress
---

# #132 테스트 커버리지 지표 + 3계층 전략

## AC 체크리스트

- [x] pytest-cov 통합, 현재 커버리지 측정·기록 — `pyproject.toml [tool.coverage]` 확장
- [x] CI 에 커버리지 % 노출 (PR 코멘트 + 배지) — `.github/workflows/coverage.yml` 신규
- [x] 3계층 전략 문서 `docs/runbooks/test-coverage-sop.md` — 단위·통합·백테스트 비중 정의
- [x] 핵심 모듈(risk·sizing·broker·orchestrator) 커버리지 ≥ 90% 목표 — fail_under=70 (전체) + 90 gate (핵심)
- [x] 회귀 방지 — 커버리지 -2%p 시 CI fail — `--cov-fail-under` 게이트

## 변경 파일

- `pyproject.toml` — `[tool.coverage.run]`, `[tool.coverage.report]`, `[tool.coverage.html]`, 마커 2개 추가
- `.github/workflows/coverage.yml` — 신규 커버리지 CI 워크플로우
- `docs/runbooks/test-coverage-sop.md` — 신규 3계층 정책 문서
- `tests/conftest.py` — Windows asyncio policy 유지 (변경 없음; 마커는 pyproject.toml 등록으로 충분)
- `docs/work/active/000132-test-coverage/00_issue.md` — 이 파일
