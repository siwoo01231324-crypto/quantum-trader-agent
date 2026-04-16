---
type: work-done
id: 01_plan
name: "#61 구현 플랜"
status: done
---

# 01_plan — #61 리서치 스프린트 2 (프로젝트 차별화)

> ⚠️ 이 문서는 AC 체크리스트만 포함된 **초안**이다.
> 구현 시작 전 `/plan 61` 으로 구체 구현 계획을 채울 것.
>
> **CLAUDE.md 의 "볼트 사전조회 필수" 규칙** 에 따라, 각 신규 research 집필 전에
> `grep -ri` / MCP `search` / SPARQL 로 기존 노트에 해당 주제가 커버되어 있는지
> 반드시 먼저 확인한다 (2026-04-16 #60 재발 방지).

## AC 체크리스트

### A. 신규 research 4개 (`docs/background/22~25-*.md`)
- [ ] `22-quantum-algorithm-readiness.md` — IBM Quantum · AWS Braket 실행 가능 범위, QAOA / QAE 고전 대비 breakeven
- [ ] `23-graphrag-for-trading-vault.md` — BM25 + embedding + SPARQL 하이브리드 retrieval, 볼트 → LLM 컨텍스트 패턴
- [ ] `24-llm-agent-safety-finance.md` — 환각 방지·tool-use 승인·audit trail·prompt injection 방어·금융 특화 가드레일
- [ ] `25-fibo-alignment.md` — FIBO ↔ `trading.ttl` 대조, 차용 가능 클래스·속성 매핑

### B. 위키링크 백필
- [ ] 각 신규 research 에 관련 `[[id]]` 5개 이상
- [ ] 기존 `14-quantum-poc-design`, `15-llm-agent-layer`, `ontology-primer`, `shacl-rules` 에 역참조 추가

### C. 검증
- [ ] `scripts/check_invariants.py --strict` 통과
- [ ] 출처 (arXiv·공식 문서·백서) 각 노트 하단 명시
- [ ] `docs/background/.ai.md` 최신화

## 구현 순서 (초안 — `/plan` 에서 구체화)

1. **사전조회** (CLAUDE.md 규칙) — 각 주제어로 볼트 grep·MCP search 먼저
2. 팩트 리서치 → 출처 확보
3. research 타입 프론트매터 준수, 본문에 `[[id]]` 5개 이상
4. 역참조 추가
5. `scripts/ontology_sync.py --write` 실행
6. 불변식 통과 확인
