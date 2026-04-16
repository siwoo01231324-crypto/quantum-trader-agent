---
type: work-done
id: 01_plan
name: "#60 구현 플랜"
status: active
---

# 01_plan — #60 리서치 스프린트 1 (볼트 위키링크 백필 + 진짜 퀀트 갭)

> 2026-04-16 스코프 재정의: 원래 AC 의 `16/17/18/21` 은 기존 노트 ([[07-market-microstructure-basics]], [[tax-automation]], [[12-validation-protocol]], [[execution-algorithms]]) 가 이미 커버하고 있어서 중복. 남은 진짜 gap 인 `19` / `20` 두 개 + 볼트 위키링크 백필 (메인) 으로 전환.

## AC 체크리스트

### A. 신규 research (2개)
- [x] [[19-portfolio-risk]] — 상관·Ledoit-Wolf·CVaR·팩터 노출
- [x] [[20-position-sizing]] — Kelly·Half Kelly·Vol Targeting·ERC·HRP

### B. 위키링크 백필
- [x] 배경 노트 07~15 에 "관련 노트" 섹션 추가
- [x] 스펙 (execution-algorithms · tax-automation · risk-rule-dsl · kill-switch-dr · observability · data-lake-schema) 에 "관련 노트" 섹션 추가
- [x] 시드 (momo-btc-v2 · rsi-divergence · max-drawdown-5pct · kill-switch-runbook) 에 "관련 노트" 섹션 추가
- [x] work-done 28개 + work-active 5개 → 구현 대상 spec 백링크 추가 (스크립트 일괄)
- [x] `mcp-setup.md` 프론트매터 추가 (타입 누락 수정)

### C. 측정·검증
- [x] 고립 노트 비율: 2026-04-16 시작 94% → 종료 4.3% (AC 목표 40% 대비 대폭 달성). 상세는 [[02_graph-stats]] 참고
- [x] `scripts/check_invariants.py --strict` 통과 (64 노트)
- [ ] `scripts/ontology_sync.py --write` → `instances.ttl` 갱신

### D. 부수 개선
- [x] CLAUDE.md "조사·리서치 규칙" 에 "볼트 사전조회 필수" 추가 — #60 초기에 기존 노트를 못 보고 중복 research 를 제안한 사고 재발 방지

## 관련 노트

- [[19-portfolio-risk]] — 본 스프린트 신규 research 1
- [[20-position-sizing]] — 본 스프린트 신규 research 2
- [[01-research-plan]] — 본 sprint 의 출발점 "17. 포지션 사이징·켈리·변동성 타겟팅 — 신규 이슈 제안"
- [[02_graph-stats]] — 고립 노트 비율 재측정 결과
- [[00_issue]] — 본 이슈 본문
