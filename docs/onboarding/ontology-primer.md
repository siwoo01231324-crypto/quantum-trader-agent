---
type: onboarding
id: ontology-primer
name: "온톨로지 입문"
---

# 온톨로지 입문

트레이딩 지식을 RDF/OWL 로 형식화해 SPARQL 로 질의 가능한 지식 그래프를 만드는 것이 목표다. 본 가이드는 Turtle 읽는 법, SPARQL 기본 문법, 그리고 `ontology_sync.py` 사용법을 다룬다.

## 1. Turtle 읽는 법

Turtle 은 RDF 직렬화 포맷 중 가장 사람에게 읽기 쉬운 형식이다.

```turtle
@prefix qta: <https://siwoo.dev/qta/ontology#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

qta:Strategy a owl:Class ;
    rdfs:label "Strategy" .

qta:usesSignal a owl:ObjectProperty ;
    rdfs:domain qta:Strategy ;
    rdfs:range qta:Signal .
```

구성요소:
- `@prefix` : 네임스페이스 단축어
- `subject predicate object .` : 한 트리플 (문장 종결 `.`)
- `;` : 같은 subject 에 대한 여러 predicate 이어쓰기
- `a` : `rdf:type` 의 약어
- `<...>` : 전체 IRI

## 2. SPARQL 기본

```sparql
PREFIX qta: <https://siwoo.dev/qta/ontology#>

SELECT ?s ?label
WHERE {
  ?s a qta:Strategy ;
     qta:status "live" .
  OPTIONAL { ?s rdfs:label ?label }
}
```

핵심 키워드:
- `SELECT` / `ASK` / `CONSTRUCT`
- `WHERE { ... }` 패턴 매칭
- `?x` : 변수
- `OPTIONAL { ... }` : 없어도 매치
- `FILTER NOT EXISTS { ... }` : 음수 제약
- `ORDER BY`, `LIMIT`

## 3. `ontology_sync.py` 사용법

### 기본 흐름
1. 프론트매터를 가진 md 파일 작성
2. `python scripts/ontology_sync.py --write` 실행 → `docs/ontology/instances.ttl` 생성
3. SPARQL 쿼리 실행

### CLI
```bash
# 차이 확인만
python scripts/ontology_sync.py --check

# instances.ttl 재생성
python scripts/ontology_sync.py --write
```

### 의존성 설치
```bash
pip install python-frontmatter rdflib PyYAML
```

## 4. SPARQL 실행 예시 (rdflib)

```python
from rdflib import Graph

g = Graph()
g.parse("docs/ontology/trading.ttl", format="turtle")
g.parse("docs/ontology/instances.ttl", format="turtle")

query = open("docs/ontology/queries/live_strategies.rq").read()
for row in g.query(query):
    print(row)
```

## 5. 프리셋 쿼리

- `docs/ontology/queries/live_strategies.rq` : 라이브 전략 리스트
- `docs/ontology/queries/critical_violations.rq` : critical 규칙 위반 incident
- `docs/ontology/queries/strategy_without_tests.rq` : Backtest 누락된 Strategy

## 6. T-Box vs A-Box

| 구분 | 파일 | 내용 |
|------|------|------|
| T-Box (스키마) | `trading.ttl` | 클래스·프로퍼티 정의 |
| A-Box (인스턴스) | `instances.ttl` | 실제 노트 데이터 (자동 생성) |

## 참조
- 스펙: `docs/ontology/trading.ttl`
- 동기화 스크립트: `scripts/ontology_sync.py`
- rdflib 공식: https://rdflib.readthedocs.io/
- SPARQL 1.1: https://www.w3.org/TR/sparql11-query/

## 관련 노트

- [[shacl-rules]] — 본 온톨로지 기반 SHACL 제약 카탈로그
- [[frontmatter-guide]] — 프론트매터가 `ontology_sync.py` 로 A-Box 에 반영
- [[25-fibo-alignment]] — 산업표준 FIBO 온톨로지와의 대조·차용 전략
- [[23-graphrag-for-trading-vault]] — 이 온톨로지를 LLM retrieval 에 활용하는 설계
- [[15-llm-agent-layer]] — LLM 에이전트가 온톨로지를 소비하는 경로
