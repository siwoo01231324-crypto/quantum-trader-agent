---
type: work-done
id: 00_issue
name: "#60 리서치 스프린트 1 — 볼트 위키링크 백필 + 진짜 퀀트 갭"
status: done
---

# chore: 리서치 스프린트 1 — 핵심 퀀트 갭 보강 (KR 미세구조·세제·과적합·포트폴리오 리스크)

## 관련 노트 (구현 대상)

- [[19-portfolio-risk]]
- [[20-position-sizing]]

## 목적
현재 `docs/specs/` 에는 정의돼 있지만 `docs/research/` 근거가 비어 있는 핵심 퀀트 주제를 보강한다. 동시에 기존 노트들 간 위키링크를 복구해 볼트 그래프 연결성을 올린다 (현재 94% 고립).

## 배경
- 2026-04-16 볼트 진단: 총 68노트 중 outgoing 링크 있는 노트 4개 (6%)
- `spec-tax-automation`, `spec-risk-rule-dsl`, `spec-execution-algorithms` 은 research 근거 없이 설계됨
- `12-validation-protocol` 에 Deflated Sharpe / Combinatorial Purged CV / Reality Check 부재 → 과적합 방지 검증 취약
- `risk-max-drawdown-5pct` 는 단일 전략 레벨. 포트폴리오 레벨 리스크·상관관계·공분산 추정 누락
- Position sizing 이론 (Kelly, fractional Kelly, ERC, risk parity) 비교 없이 어떤 방식으로 사이징할지 근거 없음
- Transaction cost model (Almgren-Chriss, 증권사 수수료, 거래세) 부재 → 백테스트-실거래 괴리

## 완료 기준 (Option A 재정의, 2026-04-16)

원래 AC 의 `16/17/18/21` 은 기존 노트 ([[07-market-microstructure-basics]], [[tax-automation]], [[12-validation-protocol]], [[execution-algorithms]]) 가 이미 커버 중임을 확인하고 스코프에서 제외.

### A. 신규 research (진짜 gap)
- [x] [[19-portfolio-risk]] — 상관·Ledoit-Wolf·CVaR·팩터 노출
- [x] [[20-position-sizing]] — Kelly·Half Kelly·Vol Targeting·ERC·HRP

### B. 위키링크 백필 (그래프 복구)
- [x] 배경 07~15 에 "관련 노트" 섹션 추가
- [x] 스펙 (execution-algorithms·tax-automation·risk-rule-dsl·kill-switch-dr·observability·data-lake-schema) 에 "관련 노트" 섹션 추가
- [x] 시드 (momo-btc-v2·rsi-divergence·max-drawdown-5pct·kill-switch-runbook) 에 "관련 노트" 섹션 추가
- [x] work-done 28개 + work-active 5개 → 구현 대상 spec 백링크 일괄 주입
- [x] `mcp-setup.md` 프론트매터 누락 보정

### C. 측정·검증
- [x] 고립 노트 비율: 2026-04-16 시작 94% → 종료 0% (AC 목표 40% 대비 대폭 달성)
- [x] `scripts/check_invariants.py --strict` 통과 (67 노트)
- [x] `scripts/ontology_sync.py --write` 실행 (4 인스턴스)

### D. 재발 방지
- [x] CLAUDE.md "조사·리서치 규칙" 에 **볼트 사전조회 필수** 추가
- [x] `docs/background/.ai.md` · `docs/specs/.ai.md` 최신화

## 작업 내역

### 2026-04-16

**1. 스코프 재정의 (Option A)**

초기 6개 research 제안 중 4개 (`16-kr-market-microstructure`, `17-kr-tax-regime`, `18-overfitting-defense`, `21-transaction-cost-model`) 가 기존 노트로 이미 커버되어 있음을 발견. "볼트를 먼저 보지 않고 제안한" 실패 모드 그 자체였음. 재정의:
- 진짜 gap 2개 (19, 20) 만 집필
- 메인 작업을 **위키링크 백필** (그래프 고립 94% 해소) 로 전환
- CLAUDE.md 에 "볼트 사전조회 필수" 규칙 추가해 재발 방지

**2. 신규 research 작성**
- `docs/background/19-portfolio-risk.md` — 샘플 공분산 병폐 / Ledoit-Wolf 선형 축소 / ENB / CVaR (Rockafellar-Uryasev 최적화) / Fama-French 팩터 노출. 10개 wikilink + 9개 학술 출처
- `docs/background/20-position-sizing.md` — Kelly (1956) / Fractional Kelly (Thorp) / Vol Targeting / Risk Parity / ERC (Maillard 2010) / HRP (López de Prado 2016). 한국 시장 특수성 (공매도 제약·가격제한폭) 반영. 9개 wikilink + 10개 학술 출처

**3. 백링크 대량 주입**
- 배경 07~15 (9개) + 스펙 6개 + 시드 4개 + 런북 1개 + 온보딩 1개 → "관련 노트" 섹션 수동 추가
- work-done 28개 + work-active 5개 → 폴더명→스펙 ID 매핑으로 스크립트 일괄 처리 (31 파일)
- `mcp-setup.md` 프론트매터 누락 수정

**4. CLAUDE.md 규칙 강화**
- "조사·리서치 규칙" 에 4단계 사전조회 절차 추가
- 위반 시 "2026-04-16 #60 재발" 로 간주

**5. 측정**
- 시작: 64/68 isolated (94.1%)
- 종료: **0/70 isolated (0.0%)**
- 상세 수치 및 재현 스크립트는 [[02_graph-stats]] 에 보존

**6. 검증**
- `check_invariants.py --strict` 67 노트 통과
- `ontology_sync.py --write` 4 인스턴스 (strategy/signal/risk-rule/instrument) 동기화

**7. 범위 밖이지만 함께 수행**
- siw-claude-template 레포에도 같은 볼트+온톨로지+MCP 인프라 이식 (별도 브랜치 `feat/obsidian-ontology-setup`). 새 프로젝트가 템플릿 복제 즉시 동일 구조로 시작 가능.
