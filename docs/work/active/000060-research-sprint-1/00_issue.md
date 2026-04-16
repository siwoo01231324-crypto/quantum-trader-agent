---
type: work-done
id: 00_issue
name: "#60 리서치 스프린트 1 — 볼트 위키링크 백필 + 진짜 퀀트 갭"
status: active
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

## 완료 기준
- [ ] 신규 research 노트 6개 작성 (`docs/research/16~21-*.md`)
  - [ ] `16-kr-market-microstructure.md` — KRX 상하한가·동시호가·서킷브레이커·사이드카·공매도 규제
  - [ ] `17-kr-tax-regime.md` — 양도세·배당세·금투세·대주주 요건·해외주식 세법
  - [ ] `18-overfitting-defense.md` — Deflated Sharpe Ratio, Combinatorial Purged CV, White's Reality Check, SPA test
  - [ ] `19-portfolio-risk.md` — 상관관계 기반 집중도 제한, Ledoit-Wolf shrinkage, 섹터·팩터 노출
  - [ ] `20-position-sizing.md` — Kelly · fractional Kelly · ERC · risk parity 비교
  - [ ] `21-transaction-cost-model.md` — 한국 증권사 수수료 구조, 거래세, Almgren-Chriss 슬리피지
- [ ] 각 신규 research 는 관련 `spec-*` 노트를 `[[위키링크]]` 로 참조
- [ ] 기존 `spec-execution-algorithms`, `spec-tax-automation`, `spec-risk-rule-dsl`, `12-validation-protocol` 본문에 `참고 리서치: [[...]]` 섹션 추가
- [ ] `scripts/check_invariants.py --strict` 통과
- [ ] 출처 (논문·KRX 공시·법령) 각 노트 하단 명시 — CLAUDE.md "조사·리서치 규칙"

## 구현 플랜
1. 각 주제별로 1차 팩트 리서치 (web 검색 + 기존 레퍼런스) → 출처 확보
2. `docs/schemas/note-schemas.md` 의 research 타입 프론트매터 준수
3. 본문에 기존 spec·research 노트 `[[위키링크]]` 를 5개 이상 걸어 그래프 연결 복구
4. 해당 `spec-*` 노트에 역방향 참조 추가 (백링크)
5. `scripts/ontology_sync.py --write` 실행 → instances.ttl 반영

## 개발 체크리스트
- [ ] 해당 디렉토리 .ai.md 최신화 (`docs/research/.ai.md`, `docs/specs/.ai.md`)
- [ ] 각 research 노트 출처 블록 (근거 없는 주장 금지)
- [ ] 백링크 복구 후 `docs/dashboards/` 에서 고립 노트 수 재측정

## 작업 내역


