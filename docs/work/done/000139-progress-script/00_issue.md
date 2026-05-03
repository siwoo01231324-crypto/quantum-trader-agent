# chore: 진척도 자동 갱신 스크립트 (gh issue list → 백서 §11-3 재생성)

## 배경
현 §11-3 표는 수동 작성. 신규 이슈 추가·머지 시 매번 수동 업데이트 — 운영 비용. 본 이슈 #86 스코프 외로 명시됨.

## Phase / 월 10% 컨텍스트
- 본 이슈는 Phase 무관 운영 도구
- 월 10% 목표와 직접 관련 없음

## AC
- [ ] scripts/update_progress_table.py — gh CLI 호출 → 표 생성
- [ ] 백서 §11-3 표 자동 갱신 (sentinel 마커 사이 영역만 교체)
- [ ] CI 또는 정기 cron — 갱신 PR 자동 생성
- [ ] 단위 테스트 (mock gh response → 표 생성 검증)

## 의존성·참고
- 후행: Whitepaper v0.2 작성 시 활용
- 백서 §11-3 / docs/work/active/000086-master-plan/01_plan.md §9
