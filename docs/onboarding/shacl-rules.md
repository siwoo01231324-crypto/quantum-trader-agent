---
type: onboarding
id: shacl-rules
name: SHACL 도메인 제약 가이드
---

# SHACL 도메인 제약 가이드

> 프론트매터 필드 존재 여부를 넘어서, **도메인 의미 규칙**을 SHACL 로 강제한다.
> 예: "라이브 전략은 반드시 risk-rule 을 가져야 한다", "P0 Incident 는 affected_strategies 기록 필수".

## 왜 SHACL 인가
- 기존 `scripts/check_invariants.py` 는 필드 존재·참조 무결성만 검증.
- 도메인 규칙 위반(예: 리스크 규칙 없는 라이브 전략)은 사람 눈으로만 잡힘.
- SHACL (Shapes Constraint Language) 은 RDF 위에서 제약을 선언하는 W3C 표준.
- 이미 `docs/ontology/instances.ttl` 이 있으므로 자연스럽게 결합.

## 규칙 카탈로그

| # | Shape | 대상 | 요지 |
|---|-------|------|------|
| 1 | `LiveStrategyRiskRuleShape` | `qta:LiveStrategy` | `appliesRule` 최소 1건 |
| 2 | `LiveStrategySharpeShape` | `qta:LiveStrategy` | `sharpeBt` 필수 |
| 3 | `IncidentCriticalPostmortemShape` | `qta:Incident` | CriticalRule 위반 + 48h 경과 → PostMortem 연결 필수 |
| 4 | `BacktestPeriodShape` | `qta:Backtest` | `periodStart < periodEnd` |
| 5 | `SignalLookbackShape` | `qta:Signal` | `lookback > 0` (정수) |
| 6 | `RiskRuleThresholdRangeShape` | `qta:RiskRule` | `threshold ∈ [0, 1]` |
| 7 | `StrategyTimeframeEnumShape` | `qta:Strategy` | `timeframe ∈ {1m, 5m, 15m, 1h, 4h, 1d}` |
| 8 | `InstrumentVenueEnumShape` | `qta:Instrument` | `venue ∈ {binance, upbit, krx, ibkr}` |
| 9 | `IncidentP0AffectedShape` | `qta:Incident` | `severity=P0` → `affectsStrategy` 최소 1건 |
| 10 | `PostmortemFinalActionItemShape` | `qta:PostMortem` | `status=final` → `hasActionItem` 최소 1건 |

각 shape 은 한국어 `sh:message` 로 위반 사유를 설명한다.

## 로컬 검증

```bash
# 1. 의존성 설치 (최초 1회)
pip install pyshacl rdflib python-frontmatter

# 2. instances.ttl 최신화
python scripts/ontology_sync.py --write

# 3. SHACL 만 빠르게 검증
python scripts/shacl_validate.py --strict

# 4. 모든 불변식 (프론트매터/위키링크/SHACL) 통합 검증
python scripts/check_invariants.py --strict
```

## 새 규칙 추가 절차

1. `docs/ontology/shapes.ttl` 편집 — 새 `qta:<Name>Shape a sh:NodeShape` 정의 추가.
   - 한국어 `sh:message` 필수.
   - enum/범위/`sh:minCount` 등은 `sh:property [...]` 형태, 조건부 제약은 `sh:sparql`.
2. 필요한 property 가 `trading.ttl` 에 없으면 추가 후 `scripts/ontology_sync.py` 의 핸들러도 갱신.
3. `tests/fixtures/shacl/rule_<NN>_<slug>_violates.ttl` · `..._compliant.ttl` 쌍 작성.
4. `tests/test_shacl.py` 의 `RULE_TO_SHAPE` 에 매핑 추가.
5. `pytest tests/test_shacl.py -v` 로 22+ 케이스 전부 통과 확인.
6. 본 문서의 규칙 카탈로그 표 업데이트.

## 위반 디버깅

CI 로그에 `[shacl] <ShapeName> · <focus_node>: <message>` 형식으로 출력된다.
`focus_node` 는 `https://siwoo.dev/qta/instance/<id>` 형태 IRI 이며, `<id>` 는 프론트매터의 `id` 필드와 일치한다.
해당 `.md` 파일을 열어 `id` 로 역추적해 수정한다.

## 제한

- OWL axiom 기반 추론은 범위 외 (별도 이슈로 관리).
- 실시간 검증은 제공하지 않는다 — 배치/CI 단계에서만 동작.
- `xsd:dateTime` 비교는 `NOW()` 기반이므로 시간대/서버 시간 차이에 민감. 48h 같은 시간 기반 제약은 느슨하게 해석할 것.

## 참고
- SHACL W3C 사양: https://www.w3.org/TR/shacl/
- pyshacl: https://github.com/RDFLib/pySHACL
- `docs/ontology/.ai.md` · `docs/schemas/note-schemas.md`
