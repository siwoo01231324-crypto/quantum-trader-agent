---
type: work-done
id: 000045-agents-md-tree-00-issue
name: "[chore] AGENTS.md 레포 구조 트리 최신화"
status: done
---

# [chore] AGENTS.md 레포 구조 트리 최신화

## 목적
AGENTS.md의 레포 구조 트리를 Phase 0-1 + Phase 2+ 이슈 완료 후 실제 상태로 업데이트한다. `{{PROJECT_NAME}}` 플레이스홀더도 실제 이름으로 치환.

## 배경
Phase 2+ 이슈(#19~#30) 12개 PR이 머지되면 src/, tests/, policies/, grafana/, loki/, prometheus/, docs/runbooks/ 등 신규 디렉토리가 대거 추가된다. AGENTS.md는 레포 목차 역할이므로 최신 구조 반영이 필요.

## 완료 기준
- [ ] `{{PROJECT_NAME}}` → `quantum-trader-agent` 치환
- [ ] 레포 구조 트리에 신규 디렉토리 전부 반영 (src/, src/data_lake/, src/risk/, src/execution/, src/observability/, src/ops/, src/tax/, tests/, policies/, grafana/, loki/, prometheus/, docs/background/, docs/runbooks/, docker-compose.yml)
- [ ] 핵심 문서 링크 섹션도 최신화 (`docs/background/` 추가)

## 구현 플랜
1. master 최신 상태에서 트리 확인
2. AGENTS.md 트리 블록 재작성
3. 커밋 + PR

## 개발 체크리스트
- [ ] 해당 디렉토리 .ai.md 최신화 (루트에는 .ai.md 없으므로 해당없음)


## 작업 내역

