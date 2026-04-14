---
type: work-done
id: 000047-obsidian-ontology-00-issue
name: "feat: Obsidian 지식볼트 + 트레이딩 온톨로지 구현"
status: done
---

# feat: Obsidian 지식볼트 + 트레이딩 온톨로지 구현

## 사용자 관점 목표

트레이더/개발자가 `docs/` 를 Obsidian으로 열면 전략·신호·리스크·백테스트·인시던트가 그래프로 시각화되고, Dataview로 현황을 즉시 쿼리할 수 있으며, 도메인 온톨로지(Turtle/RDF) 기반으로 교차 질의(SPARQL)가 가능하다. LLM 에이전트(Claude Code + 추후 MCP)가 이 구조화된 지식볼트를 맥락으로 활용해 전략 설계·회고·리스크 점검을 더 정확하게 지원한다.

## 배경

### 왜 필요한가
- 현재 `docs/specs`·`docs/work/active|done` 은 마크다운 나열 상태 → 전략·리스크·백테스트 간 **관계**가 명시되지 않음
- 전략이 늘어나면 "이 전략은 어떤 신호를 쓰고 어떤 리스크 규칙을 위반할 수 있나?" 같은 교차 질의 불가
- LLM 에이전트가 문서 관계를 추론하려면 ① 구조화 프론트매터 ② 링크 그래프 ③ 도메인 스키마(온톨로지) 필요

### 이 이슈가 만드는 결과물
1. Obsidian에서 `docs/` 그래프뷰로 전략-신호-리스크 관계 시각화
2. Dataview로 "라이브 전략 리스트", "최근 30일 인시던트-위반규칙" 즉시 쿼리
3. `docs/ontology/trading.ttl` 도메인 스키마 + SPARQL 추론 쿼리
4. 프론트매터 스키마 문서화로 노트 생성 일관성 보장
5. CI에서 프론트매터·온톨로지 유효성 검증

## 범위

### 포함 (In scope)
- `docs/` Obsidian 볼트 세팅 (`.obsidian` 설정, 필수 플러그인 목록)
- 노트 타입별 프론트매터 스키마 (Strategy/Signal/RiskRule/Instrument/Backtest/Incident/PostMortem)
- 기존 문서 대표 3~5건 마이그레이션 샘플
- `docs/ontology/trading.ttl` — Turtle 도메인 온톨로지 초안
- `scripts/ontology_sync.py` — 프론트매터 ↔ RDF 변환
- `docs/dashboards/` — Dataview 대시보드 템플릿
- 그래프뷰 색상·필터 설정 (`.obsidian/graph.json`)
- CI 검증: 프론트매터 스키마 / TTL 문법 / 링크 무결성
- README·온보딩 문서 갱신

### 제외 (Out of scope — 후속 이슈)
- MCP 서버로 볼트 노출
- LLM 에이전트 자동 노트 생성·갱신
- Protégé·GraphDB 외부 툴 연동
- SHACL 제약 검증 (v2)
- Obsidian Publish·Sync 유료 기능

## 완료 기준

- [ ] `docs/` 를 Obsidian으로 열어 그래프뷰에 전략·신호·리스크 노드가 색상별로 표시됨
- [ ] 7개 노트 타입 프론트매터 스키마가 `docs/schemas/note-schemas.md` 에 문서화됨
- [ ] 기존 문서 중 대표 3~5건이 스키마대로 마이그레이션됨
- [ ] `docs/ontology/trading.ttl` 이 rdflib로 파싱 에러 없이 로드됨
- [ ] `python scripts/ontology_sync.py --write` 가 정상 실행되어 `instances.ttl` 생성
- [ ] 최소 3개 SPARQL 쿼리(`live_strategies`, `critical_violations`, `strategy_without_tests`) 가 결과 반환
- [ ] `docs/dashboards/` 에 Dataview 대시보드 4건 이상, 옵시디언에서 정상 렌더링
- [ ] CI에서 프론트매터 스키마·TTL·링크 무결성 검증이 실패 조건으로 동작
- [ ] `docs/onboarding/` 문서 3건 + `AGENTS.md` 갱신 + 신설 디렉토리 `.ai.md` 완비
- [ ] README에 "지식볼트·온톨로지" 섹션 추가

## 구현 플랜

### R1. Obsidian 볼트 설정
- `.obsidian/` 디렉토리 생성 (커밋 포함)
- 필수 플러그인: Dataview, Templater
- 선택 플러그인: Obsidian Git, Smart Connections
- `.obsidian/app.json`: 위키링크 우선
- `.obsidian/graph.json`: 폴더별 색상 (specs=파랑, risk-rules=빨강, work/done=회색)
- `.gitignore`: `workspace.json`, `workspace-mobile.json` 제외

### R2. 프론트매터 스키마 (docs/schemas/note-schemas.md)

**Strategy** (`docs/specs/strategies/`)
```yaml
---
type: strategy
id: momo-btc-v2
name: BTC Momentum v2
status: draft|backtest|paper|live|retired
instruments: [BTCUSDT]
timeframe: 15m
uses_signals: [rsi-divergence, volume-spike]
risk_rules: [max-drawdown-5pct, position-limit-10pct]
owner: siwoo
created: 2026-04-14
sharpe_bt: 1.8
sharpe_live: null
tags: [momentum, crypto]
---
```

**Signal** (`docs/specs/signals/`)
```yaml
---
type: signal
id: rsi-divergence
inputs: [close, volume]
lookback: 14
source_model: ml-rsi-clf-v1
tags: [technical]
---
```

**RiskRule** (`docs/specs/risk-rules/`)
```yaml
---
type: risk-rule
id: max-drawdown-5pct
severity: critical|warn
scope: portfolio|strategy|instrument
threshold: 0.05
action: halt|reduce|alert
---
```

**Instrument** (`docs/specs/instruments/`)
```yaml
---
type: instrument
id: BTCUSDT
asset_class: crypto-spot
venue: binance
tick_size: 0.01
---
```

**Backtest** (`docs/work/done/backtests/`)
```yaml
---
type: backtest
id: bt-2026-04-10-momo-btc-v2
strategy: momo-btc-v2
period: [2024-01-01, 2026-04-01]
metrics:
  sharpe: 1.82
  mdd: -0.048
  trades: 412
artifacts: [reports/bt-2026-04-10.html]
---
```

**Incident / PostMortem** (`docs/work/incidents/`)
```yaml
---
type: incident
id: inc-2026-04-12-slippage
occurred: 2026-04-12T14:30:00+09:00
severity: P2
affected_strategies: [momo-btc-v2]
violated_rules: [max-drawdown-5pct]
root_cause: 유동성 급감 구간 시장가 진입
postmortem: work/incidents/pm-2026-04-12.md
---
```

### R3. 기존 문서 마이그레이션 샘플
- `docs/specs`·`docs/work` 하위 대표 3~5건 스키마 적용
- 링크 규약: 본문 참조는 `[[id]]` (파일명=id)
- `docs/onboarding/obsidian-migration.md` 가이드 작성

### R4. 온톨로지 (`docs/ontology/trading.ttl`)

```turtle
@prefix qta: <http://qta.local/trading#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .

# Classes
qta:Strategy a owl:Class .
qta:Signal a owl:Class .
qta:RiskRule a owl:Class .
qta:Instrument a owl:Class .
qta:Backtest a owl:Class .
qta:Incident a owl:Class .
qta:MLModel a owl:Class .

# Subclasses
qta:LiveStrategy rdfs:subClassOf qta:Strategy .
qta:CriticalRule rdfs:subClassOf qta:RiskRule .

# Object properties
qta:usesSignal a owl:ObjectProperty ;
    rdfs:domain qta:Strategy ; rdfs:range qta:Signal .
qta:appliesRule a owl:ObjectProperty ;
    rdfs:domain qta:Strategy ; rdfs:range qta:RiskRule .
qta:tradesOn a owl:ObjectProperty ;
    rdfs:domain qta:Strategy ; rdfs:range qta:Instrument .
qta:violatesRule a owl:ObjectProperty ;
    rdfs:domain qta:Incident ; rdfs:range qta:RiskRule .
qta:derivedFromModel a owl:ObjectProperty ;
    rdfs:domain qta:Signal ; rdfs:range qta:MLModel .

# Datatype properties
qta:sharpeRatio a owl:DatatypeProperty .
qta:status a owl:DatatypeProperty .
```

### R5. 프론트매터 ↔ RDF 변환 (`scripts/ontology_sync.py`)
- 입력: `docs/**/*.md` 프론트매터
- 출력: `docs/ontology/instances.ttl`
- 의존성: `python-frontmatter`, `rdflib`
- CLI: `--check` (검증) / `--write` (생성)
- SPARQL 쿼리: `docs/ontology/queries/*.rq`
  - `live_strategies.rq`
  - `critical_violations.rq`
  - `strategy_without_tests.rq`

### R6. Dataview 대시보드 (`docs/dashboards/`)
- `strategies-live.md` — 라이브 전략 테이블 (sharpe DESC)
- `risk-coverage.md` — 전략별 리스크 규칙 매트릭스
- `recent-incidents.md` — 최근 30일 인시던트
- `ml-model-usage.md` — 모델 ↔ 신호 사용 관계

### R7. CI 검증 (`scripts/check_invariants.py` 확장)
- 프론트매터 스키마: `pydantic` 또는 `jsonschema`
- 필수 필드 누락 → 실패
- 링크 무결성: `[[id]]` 참조 대상 존재 확인
- TTL 문법: rdflib 파싱
- SPARQL 스모크 테스트
- GitHub Actions: `.github/workflows/ontology-check.yml` (pull_request 트리거)

### R8. 문서·온보딩
- `docs/onboarding/obsidian-setup.md` — 설치·그래프뷰까지
- `docs/onboarding/frontmatter-guide.md` — 타입별 작성법
- `docs/onboarding/ontology-primer.md` — TTL·SPARQL 기본
- `AGENTS.md` 볼트 구조 섹션 추가
- 신설 디렉토리마다 `.ai.md`

### 실행 순서 (제안 단계)
1. **Phase 1 — 볼트 기반** (≈1일): `.obsidian/` 설정, 플러그인 목록, graph.json, 샘플 노트 렌더링 확인
2. **Phase 2 — 스키마·마이그레이션** (≈1~2일): note-schemas.md, 기존 문서 3~5건 적용, Dataview 1건 검증
3. **Phase 3 — 온톨로지** (≈1~2일): trading.ttl, ontology_sync.py, SPARQL 3건
4. **Phase 4 — 대시보드** (≈1일): dashboards 4건, 그래프뷰 색상·필터 완성
5. **Phase 5 — CI·문서** (≈1일): check_invariants 확장, ontology-check.yml, onboarding 3건, AGENTS.md·.ai.md 갱신

## 리스크 및 대응

| 리스크 | 영향 | 완화 |
|---|---|---|
| 프론트매터 스키마가 무거워 노트 작성 부담 | 채택 저조 | 필수 필드 최소화, 나머지 선택 |
| TTL/SPARQL 학습 곡선 | 진도 지연 | Phase 3 전 `ontology-primer.md` 선행 |
| 기존 문서 대량 마이그레이션 부담 | 완료 지연 | 이번 이슈는 샘플 3~5건, 전체는 후속 이슈 |
| CI 검증이 지나치게 엄격해 PR 막힘 | 개발 마찰 | warn 단계 → 안정 후 fail 전환 |
| Obsidian 플러그인 의존성 | 재현성 저하 | 필수/선택 구분, 권장 목록으로 기록 |

## 후속 이슈 (별도 생성 예정)
- Obsidian 볼트 MCP 서버 노출 (Claude Code 연동)
- 전체 docs 문서 프론트매터 일괄 마이그레이션
- SHACL 제약 기반 고급 검증
- LLM 에이전트 자동 노트 생성 (포스트모템·백테스트)
- Protégé·GraphDB 연동 (규모 확대 시)

## 참고 자료
- Obsidian Dataview: https://blacksmithgu.github.io/obsidian-dataview/
- W3C OWL 2 Primer: https://www.w3.org/TR/owl2-primer/
- rdflib Python: https://rdflib.readthedocs.io/
- 사전 조사: 본 대화 스레드의 온톨로지·옵시디언·Hermes-Agent 리서치 섹션

## 개발 체크리스트
- [ ] 테스트 코드 포함 (`scripts/ontology_sync.py` 단위 테스트, CI 검증 스크립트 테스트)
- [ ] 해당 디렉토리 `.ai.md` 최신화 (docs/, docs/schemas/, docs/ontology/, docs/dashboards/, docs/onboarding/, scripts/)
- [ ] 불변식 위반 없음 (`scripts/check_invariants.py`)



## 작업 내역

- 2026-04-14 워크트리·브랜치 생성 (feat/000047-obsidian-ontology)
