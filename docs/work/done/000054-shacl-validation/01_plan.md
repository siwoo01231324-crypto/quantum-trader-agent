# [#54] SHACL 제약 기반 고급 검증 — 구현 계획

> 작성: 2026-04-14 · 갱신: 2026-04-15

---

## 완료 기준

- [ ] `docs/ontology/shapes.ttl` 에 10종 이상 SHACL 제약 정의
- [ ] `pyshacl` 로 `instances.ttl` + `shapes.ttl` 검증 스크립트 동작
- [ ] CI 가 SHACL 위반 시 실패 (fail 모드)
- [ ] 위반 메시지가 사람이 읽기 쉬운 한국어 설명 포함
- [ ] 규칙별 단위 테스트: 위반/준수 픽스처 (`tests/test_shacl.py`)
- [ ] `docs/onboarding/shacl-rules.md` 가이드 작성

## 개발 체크리스트
- [ ] 테스트 코드 포함
- [ ] docs/ontology/.ai.md 갱신
- [ ] 불변식 위반 없음

---

## 현재 상태 스냅샷 (조사 결과)

### 기존 ontology 자산
- `docs/ontology/trading.ttl` — `Strategy`/`Signal`/`RiskRule`/`Instrument`/`Backtest`/`Incident`/`MLModel` + `LiveStrategy`/`CriticalRule` 서브클래스. postmortem 전용 클래스 **없음** (ontology_sync 에서 `qta:Incident` 으로 축약 매핑).
- 기존 properties: `qta:usesSignal`, `qta:appliesRule`, `qta:tradesOn`, `qta:violatesRule`, `qta:derivedFromModel`, `qta:backtestOf`, `qta:affectsStrategy`, `qta:sharpeRatio`, `qta:status`, `qta:severity`, `qta:threshold`, `qta:timeframe`, `qta:lookback`, `qta:occurred`, `qta:assetClass`, `qta:venue`.
- `scripts/ontology_sync.py` 는 `status=live` 시 `qta:LiveStrategy` 자동 부여, `severity=critical` 시 `qta:CriticalRule` 자동 부여.

### 스키마 vs 온톨로지 gap (이번 이슈에서 메워야 함)
- Backtest `period: [start, end]` → 현재 RDF 매핑 **없음**. 새 property `qta:periodStart`, `qta:periodEnd` (`xsd:date`) 필요.
- Strategy `sharpe_bt` → RDF 매핑 **없음**. 새 property `qta:sharpeBt` (`xsd:decimal`) 필요.
- Incident `postmortem: <id>` → RDF 매핑 **없음**. 새 property `qta:hasPostMortem` (`Incident → PostMortem`) 필요.
- PostMortem `status`, `action_items` → RDF 매핑 **없음**. 새 property `qta:hasActionItem` (`xsd:string` repeated or IRI list) 필요.
- PostMortem 전용 class → 신규 `qta:PostMortem` 클래스 추가, `ontology_sync.py` 의 postmortem 매핑 분리.
- RiskRule `scope` → RDF 매핑 **없음**. 새 property `qta:scope` (`xsd:string`) 필요.
- Strategy `status` 값: 스키마는 `draft|backtest|paper|live|retired`. 불일치 값은 enum 검증에서 반려.

### 프론트매터 실측 enum
- Strategy.`timeframe` ∈ `{1m, 5m, 15m, 1h, 4h, 1d}`.
- Strategy.`status` ∈ `{draft, backtest, paper, live, retired}`.
- Instrument.`asset_class` ∈ `{crypto-spot, crypto-perp, krx-stock, kr-fx, us-stock}`.
- Instrument.`venue` ∈ `{binance, upbit, krx, ibkr}` (+ 관대 허용 고려).
- Incident.`severity` ∈ `{P0, P1, P2, P3}` (RiskRule.`severity` 는 `critical|warn` 이므로 분리해 다뤄야 함).

---

## 구현 계획

### Phase 1 — 의존성 + 스켈레톤 파이프라인 (smoke)

**산출물**: `pyshacl` 설치 · `shapes.ttl` 최소 3 shape · `shacl_validate.py` CLI · 로컬 실행 통과.

1. `pyshacl` 설치 경로 결정:
   - 이 레포는 `requirements*.txt`/`pyproject.toml` **부재**. CI 의 `Install dependencies` 단계에 `pyshacl` 만 추가. 로컬 개발자용 주석으로 `pip install pyshacl` 를 `docs/onboarding/shacl-rules.md` 에 남긴다.
2. `docs/ontology/shapes.ttl` 신규 생성. prefix: `qta:`, `rdf:`, `rdfs:`, `xsd:`, `sh: <http://www.w3.org/ns/shacl#>`.
3. 초기 3 shape (smoke):
   - `SignalLookbackShape` — `targetClass qta:Signal`, `qta:lookback sh:minExclusive 0`, `sh:datatype xsd:integer`.
   - `StrategyTimeframeEnumShape` — `targetClass qta:Strategy`, `qta:timeframe sh:in ("1m" "5m" "15m" "1h" "4h" "1d")`.
   - `InstrumentVenueEnumShape` — `targetClass qta:Instrument`, `qta:venue sh:in ("binance" "upbit" "krx" "ibkr")`.
   - 각 shape 에 한국어 `sh:message` 1개 필수.
4. `scripts/shacl_validate.py` 신규:
   - CLI: `--data <ttl>` (기본 `docs/ontology/instances.ttl`), `--shapes <ttl>` (기본 `docs/ontology/shapes.ttl`), `--strict` (위반 시 exit 1).
   - `--ontology <ttl>` 옵션으로 `trading.ttl` 을 추가 로드 (`sh:targetClass` 매칭을 위해 rdfs 추론 허용).
   - `run_shacl(data_graph, shapes_graph, ontology_graph=None) -> list[Violation]` 퍼블릭 함수 제공.
   - `Violation` 은 dataclass: `focus_node`, `result_path`, `source_shape`, `message`, `severity`.
   - pyshacl 호출: `validate(data_graph, shacl_graph=shapes_graph, ont_graph=ontology_graph, inference='rdfs', advanced=True, allow_warnings=False)`.
   - 결과 그래프를 SPARQL 로 파싱해 한국어 포맷 출력: `[{shape}] {focus_node}\n  → {message}`.
   - `stdout` UTF-8 reconfigure (기존 스크립트와 동일 패턴).
5. 로컬 smoke 실행:
   - 기존 `instances.ttl` 로 검증 통과 확인 (현재 instances.ttl 에 이 3 규칙 위반 없음 — `timeframe=15m`, `venue=binance`, `lookback=14`).
   - 일부러 위반 픽스처 (lookback=0) 로 exit 1 확인.

**변경 대상**: `docs/ontology/shapes.ttl` (신규), `scripts/shacl_validate.py` (신규).

---

### Phase 2 — 온톨로지·sync 확장 (gap 메우기)

**산출물**: Phase 3 의 10종 제약이 참조할 새 property/class 를 `trading.ttl` 에 선언하고 `ontology_sync.py` 가 이들을 frontmatter 에서 생성하도록 확장.

1. `docs/ontology/trading.ttl` 추가:
   - `qta:PostMortem a owl:Class ; rdfs:label "PostMortem"` (신규 클래스, `qta:Incident` 와 별개).
   - `qta:postMortemOf a owl:ObjectProperty ; rdfs:domain qta:PostMortem ; rdfs:range qta:Incident` (PostMortem → Incident).
   - `qta:hasPostMortem a owl:ObjectProperty ; rdfs:domain qta:Incident ; rdfs:range qta:PostMortem` (Incident → PostMortem, 역방향 편의).
   - `qta:hasActionItem a owl:DatatypeProperty ; rdfs:domain qta:PostMortem ; rdfs:range xsd:string` (PostMortem status=final 검증용. 실측에서는 `action_items` 가 문자열 id 리스트).
   - `qta:scope a owl:DatatypeProperty ; rdfs:domain qta:RiskRule ; rdfs:range xsd:string`.
   - `qta:periodStart`, `qta:periodEnd` a `owl:DatatypeProperty ; rdfs:domain qta:Backtest ; rdfs:range xsd:date`.
   - `qta:sharpeBt a owl:DatatypeProperty ; rdfs:domain qta:Strategy ; rdfs:range xsd:decimal`.
2. `scripts/ontology_sync.py` 수정:
   - `TYPE_TO_CLASS["postmortem"] = QTA.PostMortem` 로 변경. `_add_postmortem` 핸들러 추가:
     - `qta:postMortemOf` (from `incident` field), `qta:status`, `qta:hasActionItem` (repeat literal).
     - `status=final` 태깅은 별도 subclass 없이 SHACL 에서 `qta:status = "final"` 로 필터링.
   - `_add_strategy`: `sharpe_bt` → `qta:sharpeBt` 매핑 추가.
   - `_add_risk_rule`: `scope` → `qta:scope` 매핑 추가.
   - `_add_backtest`: `period` (list[str] of len 2) → `qta:periodStart` / `qta:periodEnd` 로 분해. `period[0]`/`period[1]` 이 `date` 객체이면 그대로, 문자열이면 `xsd:date` 리터럴로.
   - `_add_incident`: `postmortem` field → `qta:hasPostMortem` (IRI) 추가.
3. `ontology_sync.py --write` 재실행 후 `instances.ttl` 갱신 여부 확인 (현재 인스턴스에 새 필드 값이 없으면 변화 최소).
4. `test_ontology_sync.py` 영향도 점검 후 필요 시 테스트 업데이트 (기존 테스트 깨지지 않도록 하위호환 유지).

**변경 대상**: `docs/ontology/trading.ttl`, `scripts/ontology_sync.py`, (영향 시) `tests/test_ontology_sync.py`.

---

### Phase 3 — 10종 SHACL 제약 + 픽스처 + 단위테스트

**산출물**: `shapes.ttl` 에 아래 10 shape, `tests/fixtures/shacl/` 에 위반/준수 픽스처, `tests/test_shacl.py` 파라미터라이즈 테스트.

#### 10 shape 정의 (확정)

| # | Shape 이름 | target | 제약 | 한국어 메시지 핵심 |
|---|-----------|--------|------|------------------|
| 1 | `LiveStrategyRiskRuleShape` | `qta:LiveStrategy` | `qta:appliesRule sh:minCount 1` | 라이브 전략은 최소 1개 risk-rule 이 연결되어야 합니다 |
| 2 | `LiveStrategySharpeShape` | `qta:LiveStrategy` | `qta:sharpeBt sh:minCount 1` | 라이브 전략은 `sharpe_bt` 필드가 필수입니다 |
| 3 | `IncidentCriticalPostmortemShape` | `qta:Incident` | `sh:sparql` — CriticalRule 위반 + `occurred` 이 48h 초과 + `qta:hasPostMortem` 없음 | CriticalRule 위반 Incident 는 48시간 내 PostMortem 연결이 필요합니다 |
| 4 | `BacktestPeriodShape` | `qta:Backtest` | `sh:sparql` — `?start >= ?end` 면 위반 | Backtest period 시작일은 종료일 이전이어야 합니다 |
| 5 | `SignalLookbackShape` | `qta:Signal` | `qta:lookback sh:minExclusive 0`, datatype `xsd:integer` | Signal lookback 은 0 보다 큰 정수여야 합니다 |
| 6 | `RiskRuleThresholdRangeShape` | `qta:RiskRule` | `qta:threshold sh:minInclusive 0 ; sh:maxInclusive 1` | RiskRule threshold 는 0 이상 1 이하여야 합니다 |
| 7 | `StrategyTimeframeEnumShape` | `qta:Strategy` | `qta:timeframe sh:in ("1m" "5m" "15m" "1h" "4h" "1d")` | Strategy timeframe 은 허용 목록에 있어야 합니다 |
| 8 | `InstrumentVenueEnumShape` | `qta:Instrument` | `qta:venue sh:in ("binance" "upbit" "krx" "ibkr")` | Instrument venue 는 허용 목록에 있어야 합니다 |
| 9 | `IncidentP0AffectedShape` | `qta:Incident` | `sh:sparql` — `severity="P0"` 이면 `qta:affectsStrategy sh:minCount 1` (Incident → Strategy 기존 property 활용) | P0 Incident 는 affected_strategies 를 최소 1건 기록해야 합니다 |
| 10 | `PostmortemFinalActionItemShape` | `qta:PostMortem` | `sh:sparql` — `status="final"` 이면 `qta:hasActionItem sh:minCount 1` | PostMortem status=final 은 action_items 를 최소 1건 포함해야 합니다 |

> 규칙 #3, #4, #9, #10 은 조건부 제약 → `sh:sparql` 또는 `sh:qualifiedValueShape` 패턴 사용. pyshacl 에서 `advanced=True` 로 SPARQL constraint 활성화.

#### 픽스처 구조

```
tests/fixtures/shacl/
├── rule_01_live_risk_rule_compliant.ttl
├── rule_01_live_risk_rule_violates.ttl
├── rule_02_live_sharpe_compliant.ttl
├── rule_02_live_sharpe_violates.ttl
├── ...
├── rule_10_postmortem_final_compliant.ttl
└── rule_10_postmortem_final_violates.ttl
```

- 각 파일은 **독립적 최소 그래프** (필요한 트리플만 3~8개).
- prefix 는 `qta:`, `inst:` 고정.
- `violates` 파일엔 위반 instance 1개, `compliant` 파일엔 규칙을 만족하는 instance 1개.

#### `tests/test_shacl.py` 골격

- pytest parametrize: `(rule_num, fixture_path, expected_violates: bool, expected_msg_fragment: str | None)`.
- 픽스처 수집: `Path('tests/fixtures/shacl').glob('rule_*_*.ttl')`, 파일명에서 `violates`/`compliant` 판정.
- 각 테스트: `shapes.ttl` + `trading.ttl` + 픽스처 로드 → `run_shacl()` → 결과 검사.
- violates 케이스: 결과 리스트에 해당 shape 이름이 포함 + 한국어 메시지 핵심어 포함.
- compliant 케이스: 결과 리스트가 해당 shape 에 대해 비어 있음 (다른 shape 위반은 허용 — 독립성).
- 공통 fixture (`pytest.fixture`): shapes/ontology 그래프 모듈 스코프 캐시.

**변경 대상**: `docs/ontology/shapes.ttl`, `tests/fixtures/shacl/*.ttl`, `tests/test_shacl.py`.

---

### Phase 4 — `check_invariants.py` 통합 + CI fail 모드

**산출물**: `check_invariants.py --strict` 가 SHACL 위반 포함, CI 에서 fail.

1. `scripts/check_invariants.py` 에 검사 5 `check_shacl()` 추가:
   - 위치: `check_ttl_parses()` 다음.
   - `scripts/shacl_validate.py` 의 `run_shacl()` 호출.
   - `instances.ttl`, `shapes.ttl`, `trading.ttl` 누락 시 경고만 반환.
   - 각 위반을 `[shacl] {shape_name} · {focus}: {message}` 포맷으로 `all_warnings` 리스트에 추가.
2. `.github/workflows/ontology-check.yml`:
   - `pip install` 라인에 `pyshacl` 추가.
   - 기존 `Check invariants (strict)` 스텝이 SHACL 까지 포함 (통합 후). 별도 스텝 불필요.
   - 안정화 위해 병행 스텝 `Run SHACL (verbose, warn-only)` 를 앞에 추가해 전체 위반 덤프 (continue-on-error: true).
3. `pyshacl` 실행 시간 30초 이하 유지 확인 (1~2초 예상).

**변경 대상**: `scripts/check_invariants.py`, `.github/workflows/ontology-check.yml`.

---

### Phase 5 — 온보딩 문서 + .ai.md 업데이트

1. `docs/onboarding/shacl-rules.md` 신규:
   - frontmatter: `type: onboarding, id: shacl-rules, name: SHACL Rules`.
   - 섹션:
     - 개요 (SHACL 이란·왜 필요한가)
     - 규칙 카탈로그 (Phase 3 표 그대로)
     - 로컬 검증법 (`python scripts/shacl_validate.py --strict`)
     - 새 규칙 추가 절차 (shapes.ttl 편집 → 픽스처 2종 → `tests/test_shacl.py` 파라미터 추가 → `docs/onboarding/shacl-rules.md` 규칙표 업데이트)
     - 위반 디버깅 (focus_node IRI → 원본 `.md` 파일 역추적)
     - FAQ (false positive, OWL 추론 범위 외 등)
2. `docs/ontology/.ai.md` 갱신:
   - 구조 트리에 `shapes.ttl` 추가.
   - 역할 섹션에 "SHACL 도메인 제약" 추가.
   - 사용법에 `python scripts/shacl_validate.py --strict` 추가.
3. `tests/.ai.md` 존재 시 `test_shacl.py` 역할 추가 (파일 존재 시만).
4. 프런트매터 마이그레이션 (`migrate_frontmatter.py`) idempotency 영향 없음을 확인.

**변경 대상**: `docs/onboarding/shacl-rules.md`(신규), `docs/ontology/.ai.md`, `tests/.ai.md`(있을 때만).

---

## Guardrails

### Must Have
- 모든 shape 에 한국어 `sh:message` 포함.
- 10 shape 각각에 위반/준수 픽스처 쌍.
- `pyshacl` 호출은 `inference='rdfs'`, `advanced=True`, `allow_warnings=False`.
- `--strict` 모드에서만 exit 1. 기본은 warn (기존 스크립트 관례 유지).
- 새 RDF property 추가 시 반드시 `trading.ttl` 에 정의 포함.
- UTF-8 출력 보장 (Windows cp949 이슈 대비 `sys.stdout.reconfigure`).

### Must NOT Have
- OWL axiom 기반 추론, closed-world 가정.
- 실시간 검증·런타임 훅 (배치/CI 만).
- `trading.ttl` 의 기존 class/property 삭제·rename (추가만 허용).
- 픽스처에 실제 운영 데이터 포함.
- CI 시간 30초 이상 증가.
- 테스트 스킵 / xfail 로 때우기 — 규칙 정의 시점에 통과해야 함.

### 주의 사항
- pyshacl 의 SPARQL constraint 는 `advanced=True` + `SHACL-AF` 지원 버전 필요. pyshacl 0.25+ 권장. 설치 시 버전 고정은 하지 않고 최신 사용.
- `instances.ttl` 은 `ontology_sync.py --write` 산출물이므로 Phase 2 확장 후 재생성이 필요할 수 있음. 본 PR 에서는 새 필드가 실제 노트에 있는 것이 없으면 변화 없음. 변화 있으면 커밋에 포함.
- `qta:` prefix 는 반드시 `https://siwoo.dev/qta/ontology#` (trailing `#` 포함) 로 일치.
- `sh:in` 의 enum 값은 실측 프론트매터 기준. 스키마에 없는 값 추가 시 shapes.ttl 업데이트 필요.
- 픽스처는 ontology(`trading.ttl`) 도 필요. 테스트에서는 `trading.ttl` + 픽스처를 data graph 로 합쳐 로드.
- `tests/test_shacl.py` 는 기존 다른 테스트(check_invariants, ontology_sync) 와 독립적으로 동작해야 함.

---

## 실행 순서 요약

1. **Phase 1** — `shapes.ttl` 3 shape + `shacl_validate.py` smoke
2. **Phase 2** — `trading.ttl` / `ontology_sync.py` 확장
3. **Phase 3** — 10 shape + 20 픽스처 + 단위테스트
4. **Phase 4** — `check_invariants.py` 통합 + CI 수정
5. **Phase 5** — 온보딩 문서 + .ai.md 갱신
6. **검증** — `python scripts/check_invariants.py --strict`, `pytest tests/test_shacl.py`, `python scripts/ontology_sync.py --check`
7. 커밋 단위: Phase 단위 커밋 (5개 커밋) → `/finish-issue` 로 PR

## 관련 노트 (구현 대상)

- [[shacl-rules]]
