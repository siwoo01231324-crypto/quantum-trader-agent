---
type: work-done
id: 00_issue
name: "#61 리서치 스프린트 2 — GraphRAG · LLM 실무 가드레일 · FIBO 대조"
status: done
---

# chore: 리서치 스프린트 2 — GraphRAG · LLM 실무 가드레일 · FIBO 대조

## 관련 노트 (구현 대상)

- [[23-graphrag-for-trading-vault]]
- [[24-llm-agent-safety-finance]]
- [[25-fibo-alignment]]

## 목적

프로젝트 정체성 ("agent" + "knowledge graph") 에 해당하는 영역의 research 를 보강하되, 기존 노트가 이미 커버하는 부분은 **중복 집필하지 않는다** (#60 사고 재발 방지).

## 배경 — 사전조회 결과 (2026-04-17)

- **양자 실용성 (원래 22)** → [[06-why-quantum-now]] + [[14-quantum-poc-design]] 이 이미 NISQ 한계·실증 3건·IBM Heron 비용·breakeven·2026 도입현황을 전체 커버. **스코프에서 제외**
- **LLM 안전성 (원래 24)** → [[15-llm-agent-layer]] §3 가 이미 7종 리스크·7종 가드레일을 개념 레벨로 정리. **재정의: 실무 구현 패턴 특화**
- **GraphRAG (23)**, **FIBO (25)** — 진짜 gap 확인

## 완료 기준

### A. 신규 research
- [x] [[23-graphrag-for-trading-vault]] — Microsoft GraphRAG·LightRAG·3-layer 하이브리드 retrieval (BM25 + 임베딩 + SPARQL). 본 프로젝트 MCP 확장 도구 3종 제안
- [x] [[24-llm-agent-safety-finance]] (재정의) — 실무 구현 패턴: (1) Structured Output (Anthropic tool use / OpenAI JSON mode), (2) OpenTelemetry Gen-AI 감사 트레일 + Langfuse, (3) Anthropic Agentic Misalignment 실사례, (4) 5계층 Eval harness, (5) 본 프로젝트 가드레일 체크리스트
- [x] [[25-fibo-alignment]] — FIBO 8개 모듈 ↔ `trading.ttl` 매핑표. 옵션 A/B/C 차용 전략 + `rdfs:seeAlso` 권장안

### B. 위키링크 백필
- [x] 3개 신규 research 에 각 5개 이상 `[[id]]`
- [x] [[15-llm-agent-layer]] · [[ontology-primer]] · [[shacl-rules]] 에 역참조 섹션 추가
- [x] `docs/background/.ai.md` 최신화

### C. 검증
- [x] `scripts/check_invariants.py --strict` 통과 (72 노트)
- [x] `scripts/ontology_sync.py --write` (4 인스턴스)
- [x] 각 노트 하단 출처 (arXiv · Anthropic·OpenAI 공식 · FIBO · OMG 등) 명시

## 작업 내역

### 2026-04-17

**1. 사전조회 (CLAUDE.md 규칙 적용)**

#60 에서 추가한 "볼트 사전조회 필수" 규칙에 따라 각 주제어를 `docs/` 에 grep 하고 기존 커버 확인:
- 22 (양자 실용성): `06-why-quantum-now` 가 NISQ / 실증 / JPMorgan·Goldman·Vanguard·Crédit Agricole / 회의론 / 2026 도입현황 전면 커버. `14-quantum-poc-design` 이 IBM Heron 비용·QAOA 벤치마크·성공기각 기준 포함. **중복 제거 판정**
- 24 (LLM 안전성): `15-llm-agent-layer` 가 7리스크·7가드레일 개념 전면 정리됨. 단, "어떻게 구현" 은 비어 있음. **실무 구현 특화로 재정의**
- 23 (GraphRAG), 25 (FIBO): 기존 커버 전무. **신규 집필**

**2. 신규 research 3건 작성**

- `23-graphrag-for-trading-vault.md` — Microsoft GraphRAG / LightRAG / HybridRAG 계보 정리, 3-layer 하이브리드 (BM25 + bge-m3 임베딩 + SPARQL 확장) 본 프로젝트 설계, MCP 확장 도구 3종 (`hybrid_search`, `expand_neighbors`, `assemble_context`) 제안, 비용·성능 수치, Phase 2~4 로드맵
- `24-llm-agent-safety-finance.md` — Anthropic tool use / OpenAI structured output 실 코드 예시, OpenTelemetry Gen-AI 시맨틱 컨벤션 + Langfuse/Helicone/Braintrust 비교, Anthropic Agentic Misalignment (2025) 실사례, 금융 특화 eval harness 5계층 (L1~L5), 본 프로젝트 가드레일 체크리스트 3영역
- `25-fibo-alignment.md` — FIBO 5개 최상위 모듈 + 12,000+ 클래스 개요, `qta:` 9 클래스 ↔ FIBO 매핑표, 3가지 차용 옵션 (Full Adoption / Selective Import / Alignment Only), 옵션 C (`rdfs:seeAlso`) 즉시 도입 권고, FIBO 설계 관례 4개 차용 제안 (IRI 계층화·`skos:definition` 필수·`owl:versionIRI`)

**3. 역참조 백필**
- `15-llm-agent-layer` → `[[23]]` · `[[24]]`
- `ontology-primer` → `[[25]]` · `[[23]]`
- `shacl-rules` → `[[25]]` · `[[23]]`

**4. 검증**
- `check_invariants --strict`: 72 노트 통과
- `ontology_sync --write`: 4 인스턴스 유지 (research 는 RDF 인스턴스화 대상 아님 — 의도된 동작)

**5. 스코프 재정의 효과**

원래 계획 4 신규 + 백링크. 실제 진행 **3 신규** (1개 스킵: 기존 06·14 중복) + **1 재정의** (24 를 실무 구현 특화로 전환) + 백링크. "사전조회 필수" 규칙이 직접 효과: **작업 시작 전** 에 재정의 → 작업 낭비 없음. #60 은 작업 도중 재정의를 발견했으니 한 사이클 만에 개선된 셈.
