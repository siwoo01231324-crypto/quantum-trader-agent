---
type: research
id: 23-graphrag-for-trading-vault
name: "GraphRAG — 지식그래프 기반 볼트 → LLM 컨텍스트 전달 패턴"
sources:
  - https://arxiv.org/abs/2404.16130
  - https://arxiv.org/abs/2410.05779
  - https://microsoft.github.io/graphrag/
  - https://docs.llamaindex.ai/en/stable/examples/property_graph/property_graph_basic/
  - https://blog.langchain.dev/graphrag-langchain/
  - https://www.pinecone.io/learn/graph-rag/
---

# GraphRAG — 지식그래프 기반 볼트 → LLM 컨텍스트 전달 패턴

> 본 프로젝트는 68+ 개 Obsidian 노트 (`docs/`) 와 RDF 온톨로지 (`docs/ontology/trading.ttl`) 를 가지고 있다. 에이전트가 자연어 질의로 관련 노트를 효과적으로 끌어와야 한다. 단순 벡터 RAG 는 **여러 홉 관계** (예: "이 전략이 쓰는 시그널이 의존하는 리스크 룰") 을 놓치고, 순수 SPARQL 은 **자연어 질의** 를 다룰 수 없다. GraphRAG 는 이 둘의 하이브리드다.

---

## 1. 문제 정의 — 왜 벡터 RAG 만으로 부족한가

### 1.1 본 프로젝트 시나리오

에이전트 질의 예시:
- "momo-btc-v2 의 리스크 체인을 알려줘"
- "2024 Q4 에 일어난 P1 이상 인시던트 중 롤백을 유발한 전략은?"
- "KRX 거래세가 영향을 주는 전략·실행 알고리즘을 전부 열거"

이들은 **multi-hop** (전략 → 시그널 → 리스크 룰 → 실패 사례) 탐색이 필요한데, 벡터 유사도만 쓰면 다음 문제가 발생한다.

1. **Context fragmentation**: 관련 정보가 여러 노트에 흩어져 있을 때 top-k 벡터 검색은 한 노트씩만 가져옴
2. **Relation blindness**: "A 가 B 를 참조한다" 라는 **관계** 는 임베딩이 직접 인코딩하지 못함
3. **Aggregation failure**: "X 에 영향받는 노트 전부" 같은 집합 질의는 벡터 검색의 top-k 와 근본적으로 다른 연산

[[15-llm-agent-layer]] 가드레일 #3 "RAG 로 근거 첨부" 를 구현하려면 단순 벡터 검색 이상이 필요하다.

### 1.2 순수 SPARQL 도 부족

`docs/ontology/trading.ttl` + `instances.ttl` 에 SPARQL 을 직접 날리면 관계 탐색은 완벽하나:

1. 자연어 질의를 SPARQL 로 변환해야 함 (NL2SPARQL 모델 필요, 정확도 불안정)
2. 본문 텍스트 검색은 RDF 그래프에 없음 — "슬리피지" 를 언급하는 노트 찾기 불가
3. 온톨로지에 없는 암묵 지식 (예: 노트 본문의 근거·예시·코드) 은 미활용

---

## 2. GraphRAG 계보 — 3가지 접근법

### 2.1 Microsoft GraphRAG (2024)

- **인덱스 단계**: LLM 으로 문서 전체에서 엔티티·관계를 추출해 지식그래프 구축 → 그래프에서 **커뮤니티(community)** 탐지 (Leiden 알고리즘) → 각 커뮤니티의 "요약(summary)" 을 LLM 생성
- **쿼리 단계**:
  - *Global Search*: 질의를 모든 커뮤니티 요약에 던지고 집계 → "전체적 시사점" 질의 답변
  - *Local Search*: 쿼리와 유사한 엔티티를 찾고 그 이웃·소속 커뮤니티·원문을 컨텍스트로 반환
- **특성**: 인덱스 비용 높음 (토큰 비용 수 십 달러 ~ 수 백), 그러나 multi-hop·aggregation 우수

### 2.2 LightRAG (2024-10)

- **핵심 차이**: 커뮤니티 요약을 만들지 않고 **low-level (엔티티) + high-level (키워드)** 두 계층 검색만 운영
- **장점**: 인덱스 비용 대폭 절감, 증분 업데이트 용이
- **단점**: "전체 요약" 질의에서 GraphRAG 에 소폭 뒤처질 수 있음

### 2.3 HybridRAG (BM25 + Embedding + Graph)

- Kevin Haworth et al. (2024) 등 — 금융 도큐먼트 RAG 에서 **BM25 (lexical) + dense embedding + KG traversal** 3자 결합이 각 단일 방법 대비 Recall@5 +15% 이상 보고
- 본 프로젝트 규모 (수십~수백 노트) 에서 가장 실용적

---

## 3. 본 프로젝트 설계 — 3-Layer Hybrid Retrieval

```
자연어 질의
   ↓
┌──────────────────────────────────────────────────┐
│ Layer 1: Lexical (BM25)                          │
│ - docs/**/*.md 본문·제목 BM25 색인 (Tantivy 등)  │
│ - 키워드·희귀 용어 매칭에 강함 ("슬리피지" 등)   │
└──────────────────────────────────────────────────┘
   ↓ top-k 후보
┌──────────────────────────────────────────────────┐
│ Layer 2: Dense Embedding (pgvector / Chroma)     │
│ - 노트 청크 임베딩 (sentence-transformers KR)    │
│ - 의미적 유사도로 개념적 관련 노트 발견           │
└──────────────────────────────────────────────────┘
   ↓ top-k 후보
┌──────────────────────────────────────────────────┐
│ Layer 3: Graph Expansion (SPARQL)                │
│ - 후보 노트의 [[wikilink]] 이웃 수집 (1~2홉)     │
│ - trading.ttl 의 qta:usesSignal / appliesRule /  │
│   violatesRule 등 관계도 동시 traverse           │
└──────────────────────────────────────────────────┘
   ↓ 병합 + reranking
┌──────────────────────────────────────────────────┐
│ Reranker (cross-encoder 또는 LLM rerank)         │
└──────────────────────────────────────────────────┘
   ↓ top-N 선택
LLM 컨텍스트 주입
```

### 3.1 BM25 레이어

- 라이브러리: `tantivy-py` (Rust 기반 빠름) 또는 `rank_bm25`
- 색인 단위: **노트 전체** (청크 아님) — 본 프로젝트 노트 대부분 < 300 라인
- 가중치: `title * 3 + frontmatter.name * 2 + body * 1`
- 한국어 토크나이저: `kiwipiepy` (Kiwi) 권장 (Okt·Mecab 대비 빠름)

### 3.2 Dense Embedding 레이어

- 모델: `intfloat/multilingual-e5-large` (KR+EN 동시) 또는 `BAAI/bge-m3` (2024, 한국어 SOTA 에 근접)
- 청킹: **섹션 단위** (H2 `## ` 기준), 청크당 200~500 토큰
- 저장: `pgvector` (1 인스턴스 KV) 또는 `chromadb` (로컬)
- 주의: 프론트매터는 임베딩에서 제외 (메타데이터로 별도 인덱싱)

### 3.3 SPARQL / Wikilink 확장 레이어

Layer 1·2 에서 얻은 후보 노트 집합 `C` 에 대해:

```sparql
# 예: 후보 노트들의 1-hop 이웃 수집
SELECT DISTINCT ?neighbor WHERE {
  VALUES ?c { inst:momo-btc-v2 inst:rsi-divergence }   # 후보들
  {
    ?c ?p ?neighbor .
    ?neighbor a ?cls .
    FILTER(?cls IN (qta:Strategy, qta:Signal, qta:RiskRule, qta:Instrument))
  } UNION {
    ?neighbor ?p ?c .
    ?neighbor a ?cls .
    FILTER(?cls IN (qta:Strategy, qta:Signal, qta:RiskRule))
  }
}
```

이웃으로 확장된 집합을 최종 rerank 후보로 포함.

### 3.4 Reranker

- **Cross-encoder**: `BAAI/bge-reranker-v2-m3` — 속도·품질 균형
- **LLM rerank**: Anthropic Claude Haiku 프롬프트 "다음 후보들이 질의와 얼마나 관련있는지 0~10 점수와 한 줄 이유를 내놓아라" — 비용 높지만 해석력 우수
- 본 프로젝트 초기 구현: cross-encoder 고정, LLM rerank 는 코파일럿 경로에만

---

## 4. 컨텍스트 조립 — "RAG Output → Prompt" 패턴

### 4.1 단순 concat 의 한계

기존 RAG 는 top-k 청크를 프롬프트에 그대로 붙인다. 본 프로젝트에서는 다음 정보를 **구조화** 해 넘긴다:

```markdown
# Retrieved Context for: <user query>

## Primary notes (top relevance)

- [[momo-btc-v2]] (strategy, Sharpe_bt=1.82)
  - 진입: [[rsi-divergence]] bullish divergence
  - 리스크: [[max-drawdown-5pct]]
  - 인시던트 이력: 2 건

## 1-hop neighbors

- [[rsi-divergence]] (signal, lookback=14)
- [[max-drawdown-5pct]] (risk-rule, threshold=0.05, severity=critical)
- [[BTCUSDT]] (instrument, venue=binance)

## Related spec

- [[execution-algorithms]] §3.1 슬리피지 모델 플러그인

## Relevant excerpts

> (from 07-market-microstructure-basics §4.1)
> KRX 가격제한폭 ±30% …

## Provenance

- Graph path: strategy → usesSignal → signal (1 hop)
- Lexical score: 0.78, Embedding score: 0.82, Rerank: 9.2/10
```

### 4.2 Provenance 필수

[[15-llm-agent-layer]] 가드레일 #3 "근거 첨부" 요건. 각 청크에 **origin note id + 섹션 + retrieval score** 를 달고 LLM 출력이 이를 재인용하도록 system prompt 에 강제.

---

## 5. Obsidian MCP 서버 확장 (`services/obsidian_mcp/`)

현재 MCP 도구 ([[mcp-setup]]) 는 7개:
`read_note`, `list_notes`, `search`, `write_note`, `append_section`, `sparql`, `graph_neighbors`

GraphRAG 구현을 위해 추가 도구 **3개** 제안:

| tool | 설명 | 의존성 |
|------|------|--------|
| `hybrid_search(query, k=10)` | 3-layer 하이브리드 retrieval + rerank 수행 | BM25 + 임베딩 인덱스 |
| `expand_neighbors(ids, depth=2)` | 기존 `graph_neighbors` 의 배치 버전 (후보 집합 일괄 확장) | 기존 로직 재사용 |
| `assemble_context(ids, query)` | 후보 ID 리스트 → §4.1 형식 구조화된 Markdown 반환 | 기존 `read_note` 조합 |

추가 도구의 **쓰기 없음** → `writes_enabled` 와 무관하게 안전.

---

## 6. 실무 구현 로드맵 (Phase 2+)

### 6.1 Phase 2 (MVP, 2주)
1. BM25 인덱스 스크립트 (`scripts/build_bm25_index.py`) — 증분 갱신 지원
2. `hybrid_search` MCP 도구 (BM25 + 그래프 확장만, 임베딩 생략)
3. 간단한 CLI 테스트 — 질의 → 컨텍스트 덤프 확인

### 6.2 Phase 3 (임베딩 추가, 2주)
4. `bge-m3` 임베딩 배치 스크립트
5. pgvector 또는 chromadb 로컬 저장
6. 3-layer hybrid_search 완성

### 6.3 Phase 4 (코파일럿 통합, 3주)
7. `assemble_context` 도구 + provenance 주입
8. `services/doc_agent/` (#53) 에 GraphRAG 경로 연결 → 백테스트/인시던트 초안 품질 개선
9. [[observability]] 에 retrieval 메트릭 추가: `qta_rag_retrieval_latency`, `qta_rag_hit_rate`

### 6.4 평가 (골든 쿼리 셋)
- 본 프로젝트용 20~50 개 골든 질의 쌍 (질의 → 기대 노트 ID 집합) 구축
- Recall@5, MRR, nDCG 로 정량 비교
- BM25 단독 vs +embedding vs +graph 순차 ablation

---

## 7. 비용·성능 가이드

| 항목 | 비용 추정 (60 노트 기준) |
|------|--------------------------|
| BM25 색인 초기 구축 | < 5 초, 메모리 < 50 MB |
| bge-m3 임베딩 (GPU T4) | 노트당 < 1 초, 전체 < 2 분 |
| pgvector 저장 | 디스크 < 50 MB |
| cross-encoder rerank (top-20) | 질의당 < 300 ms |
| 전체 retrieval 레이턴시 (E2E) | 질의당 500 ms ~ 1 s |

Microsoft GraphRAG 의 LLM 기반 "community summary" 는 본 프로젝트 규모에서는 **스킵 권장** — 노트가 수천 개 이상일 때 의미 있음.

---

## 8. 리스크 · 한계

| 리스크 | 영향 | 완화 |
|--------|------|------|
| 한국어 임베딩 품질 편차 | retrieval 정확도 저하 | bge-m3 + 하이브리드로 BM25 가 보완 |
| 그래프 확장이 노이즈 유입 | 관련 없는 노트가 컨텍스트 차지 | depth ≤ 2, rerank 후 top-N 절단 |
| 프론트매터 변경 시 인덱스 stale | 검색 누락 | CI 또는 `ontology_sync` 에 인덱스 재빌드 트리거 |
| Prompt injection (retrieved 내용에 지시문) | 악성 명령 실행 | 컨텍스트 구획 (system vs retrieved) 엄격 분리, [[15-llm-agent-layer]] 가드레일 준수 |

---

## 9. 결정 체크리스트

- [ ] Phase 2 MVP 스코프 승인 (BM25 + 그래프만)
- [ ] 임베딩 모델 선정 (`bge-m3` default, `e5-large` 대안)
- [ ] 저장소 선정 (`chromadb` 로컬 → 운영 시 `pgvector`)
- [ ] 골든 쿼리 20개 초안 작성
- [ ] MCP 서버에 `hybrid_search` 도구 추가 (별도 이슈)

---

## 관련 노트

- [[15-llm-agent-layer]] — 본 노트가 보강하는 RAG 가드레일 (§3 #3)
- [[mcp-setup]] — `services/obsidian_mcp/` 설치·확장 경로
- [[ontology-primer]] — SPARQL 레이어가 사용하는 온톨로지 기초
- [[shacl-rules]] — retrieval 대상 노트의 SHACL 검증
- [[observability]] — retrieval 메트릭 수집 대상
- [[12-validation-protocol]] — LLM 출력이 주문 경로에 들어가기 전 검증
- [[13-feature-alpha-catalog]] — GraphRAG 가 탐색할 "피처" 노트 예시
- [[data-lake-schema]] — 벡터 저장소를 parquet 외부에서 운영할 때의 경계

---

## 출처

- Edge, D. et al. (2024). *From Local to Global: A Graph RAG Approach to Query-Focused Summarization*. arXiv:2404.16130. <https://arxiv.org/abs/2404.16130>
- Microsoft GraphRAG 공식 문서 — <https://microsoft.github.io/graphrag/>
- Guo, Z. et al. (2024). *LightRAG: Simple and Fast Retrieval-Augmented Generation*. arXiv:2410.05779. <https://arxiv.org/abs/2410.05779>
- Haworth, K. et al. (2024). *Blended RAG: Improving RAG Accuracy with Semantic Search and Hybrid Query-Based Retrievers*. (NYSE/KG benchmark). <https://arxiv.org/abs/2404.07220>
- Pinecone Learning Center — *Graph RAG Overview*. <https://www.pinecone.io/learn/graph-rag/>
- LlamaIndex — *Property Graph Index Guide*. <https://docs.llamaindex.ai/en/stable/examples/property_graph/property_graph_basic/>
- LangChain — *GraphRAG with LangChain blog post* (2024). <https://blog.langchain.dev/graphrag-langchain/>
- Sentence Transformers `BAAI/bge-m3` — <https://huggingface.co/BAAI/bge-m3>
- Reranker `BAAI/bge-reranker-v2-m3` — <https://huggingface.co/BAAI/bge-reranker-v2-m3>
- Kiwi 한국어 형태소 분석기 — <https://github.com/bab2min/Kiwi>
- Tantivy Rust full-text search — <https://github.com/quickwit-oss/tantivy>
