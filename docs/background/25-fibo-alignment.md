---
type: research
id: 25-fibo-alignment
name: "FIBO 대조 — 산업표준 금융 온톨로지와 trading.ttl 매핑"
sources:
  - https://spec.edmcouncil.org/fibo/
  - https://github.com/edmcouncil/fibo
  - https://spec.edmcouncil.org/fibo/ontology/FND/
  - https://spec.edmcouncil.org/fibo/ontology/SEC/
  - https://spec.edmcouncil.org/fibo/ontology/BE/
  - https://www.omg.org/spec/EDMC-FIBO/
---

# FIBO 대조 — 산업표준 금융 온톨로지와 trading.ttl 매핑

> 본 프로젝트의 `docs/ontology/trading.ttl` 은 **트레이딩 도메인에 특화된 경량 온톨로지**다. 금융 산업에는 이미 **FIBO (Financial Industry Business Ontology)** 라는 성숙한 OMG 표준이 존재한다. 본 노트는 둘을 대조해 (1) 우리 온톨로지의 어떤 개념이 FIBO 와 일치하는지, (2) FIBO 에서 차용할 만한 부분이 어디인지, (3) 본 프로젝트 고유로 남겨야 할 영역을 정리한다.

---

## 1. FIBO 개요

- **발행기관**: EDM Council + OMG (Object Management Group)
- **라이선스**: MIT (프로덕션 모듈), Apache 2.0 (일부 provisional)
- **형식**: OWL2, Turtle 및 RDF/XML 제공, 모듈은 ODM·UML 다이어그램도 함께 공개
- **구조**: 500+ 온톨로지 파일, 12,000+ 클래스. 모듈 5개 최상위:
  - **FND** Foundations — Dates, Law, Relations, Quantities (범용)
  - **BE** Business Entities — 법인·파트너십·정부기관
  - **FBC** Financial Business and Commerce — 시장·거래소·결제
  - **SEC** Securities — 주식·채권·파생·펀드
  - **DER** Derivatives — 옵션·선물·스왑 (SEC 확장)
  - **IND** Indices and Indicators
  - **LOAN** Loans & Lending
  - **MD** Market Data
- 2025 년 현재 Production 상태 모듈 대부분은 10년+ 버전 관리 실적

---

## 2. `trading.ttl` 개요 (현재)

본 프로젝트 온톨로지 ([[ontology-primer]] 참조) 의 핵심 9 클래스:

| `qta:` 클래스 | 역할 |
|---------------|------|
| `qta:Strategy` | 거래 전략 명세 |
| `qta:Signal` | 진입·청산 신호 |
| `qta:RiskRule` | 리스크 규칙 |
| `qta:Instrument` | 거래 종목 메타 |
| `qta:Backtest` | 백테스트 실행 결과 |
| `qta:Incident` | 운영 장애·사건 |
| `qta:PostMortem` | 장애 회고 |
| `qta:MLModel` | 시그널 생성용 ML 모델 |
| `qta:LiveStrategy` / `qta:CriticalRule` | 서브클래스 |

주요 Object Properties: `usesSignal`, `appliesRule`, `tradesOn`, `violatesRule`, `derivedFromModel`, `backtestOf`, `affectsStrategy`, `hasPostMortem`.

---

## 3. 매핑표 — `qta:` ↔ FIBO

| `qta:` 클래스·속성 | FIBO 대응 | 관계 | 비고 |
|--------------------|-----------|------|------|
| `qta:Instrument` | `fibo-sec-sec-sec:Security` / `fibo-fbc-fi-fi:FinancialInstrument` | **직접 대응** | FIBO 가 훨씬 세밀 (ISIN·LEI·CUSIP 식별자·통화·발행기관 속성 포함). 단, KRX 종목 코드는 FIBO 표준 식별자가 아니므로 커스텀 필드 필요 |
| `qta:Instrument.venue` | `fibo-fbc-fct-mkt:ExecutionVenue` (SEC Market Subfacility 하위) | 대응 | FIBO 는 거래소를 첫 클래스 시민으로 보유 |
| `qta:Instrument.asset_class` | `fibo-fbc-fi-fi:AssetClass` (enum 형태) | 대응 | 본 프로젝트는 `crypto-spot, crypto-perp, krx-stock, us-stock` 등을 enum 으로. FIBO 는 Equity/Debt/Derivative/FX 대분류 |
| `qta:Strategy` | 없음 (가장 가까운 것: `fibo-be-le-cb:InvestmentFund` 의 mandate, 그러나 의미 다름) | **프로젝트 고유** | FIBO 는 "금융 실체·상품·시장" 중심, "알고리즘 전략" 은 범위 밖 |
| `qta:Signal` | 없음 | **프로젝트 고유** | 기술 분석·ML 출력 개념은 FIBO 에 없음 |
| `qta:RiskRule` | `fibo-fnd-agr-ctr:Rule` (매우 추상적) | 약한 대응 | FIBO 는 "compliance rule" 개념이 산재. 런타임 리스크 DSL 은 본 프로젝트 고유 |
| `qta:Backtest` | 없음 | **프로젝트 고유** | FIBO 는 실제 거래 기록 (trade report) 만 다룸 |
| `qta:Incident` | 없음 | **프로젝트 고유** | 운영 장애는 ITIL/SRE 도메인 |
| `qta:PostMortem` | 없음 | **프로젝트 고유** | 위와 동일 |
| `qta:MLModel` | 없음 | **프로젝트 고유** | FIBO 는 모델 개념 없음 |
| `qta:usesSignal`, `qta:appliesRule` | 없음 | **프로젝트 고유** | 위 클래스들의 관계이므로 FIBO 범위 밖 |
| `qta:tradesOn` | `fibo-fbc-pas-fpas:tradesAt` 와 유사 | 약한 대응 | 시맨틱 재사용 가능 |
| `qta:hasPostMortem` | 없음 | **프로젝트 고유** | |

**결론**: `qta:Instrument` 서브모델은 FIBO 와 **중첩이 크고**, `Strategy / Signal / Backtest / Incident / PostMortem / MLModel` 등 "퀀트 운영 관점" 은 **FIBO 범위 밖**이다.

---

## 4. 차용 전략 — 3가지 선택지

### 4.1 옵션 A: Full Adoption (FIBO 전부 import)

- 장점: 산업 표준 언어, 향후 외부 연동·공시 연결 용이
- 단점: 12,000 클래스 import → 볼트 규모 대비 과잉, 트리플 수·SPARQL 레이턴시 악화
- 본 프로젝트 **부적합**

### 4.2 옵션 B: Selective Import (SEC·MD 핵심 클래스만)

- 범위: `fibo-sec-sec-sec:Security`, `fibo-fbc-fi-fi:FinancialInstrument`, `fibo-fbc-fct-mkt:ExecutionVenue` 등 **20~30 클래스**
- 방법: FIBO Turtle 에서 해당 클래스만 subset → `docs/ontology/fibo_subset.ttl` 로 저장, 우리 `qta:Instrument` 가 이를 `rdfs:subClassOf` 선언
- 장점: 표준과 별칭 관계 유지, 볼트 가벼움
- 단점: FIBO 모듈 간 의존성 추적 필요 (한 클래스가 5~10 개 참조)

### 4.3 옵션 C: Alignment Only (매핑 선언만)

- 범위: import 없이, 우리 클래스에 `owl:equivalentClass` 또는 `rdfs:seeAlso` 로 FIBO IRI 만 명기
  ```turtle
  qta:Instrument
      rdfs:seeAlso <https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/FinancialInstruments/FinancialInstrument> .
  ```
- 장점: 0 비용, 향후 외부 SPARQL 연합 쿼리 시 단서 제공
- 단점: 시맨틱 추론 불가능, 문서화 효과만

→ **본 프로젝트 권고: 옵션 C 즉시 + 필요 시 옵션 B 선택 도입.** FIBO 는 "표준 언어" 지만 본 프로젝트의 운영 관심사 (전략·백테스트·인시던트) 와 교집합이 좁다. 주석 수준의 연결만으로도 외부 데이터 소스 매핑 시 큰 도움.

---

## 5. 실행 계획 — 옵션 C 적용

### 5.1 변경 대상
- `docs/ontology/trading.ttl` 에 `rdfs:seeAlso` 7~10 건 추가
- `docs/ontology/alignments/fibo.ttl` (새 파일) — `owl:equivalentClass` · `owl:equivalentProperty` 선언 분리 관리

### 5.2 예시 (제안)
```turtle
@prefix qta:  <https://siwoo.dev/qta/ontology#> .
@prefix fibo-fbc-fi-fi: <https://spec.edmcouncil.org/fibo/ontology/FBC/FinancialInstruments/FinancialInstruments/> .
@prefix fibo-fbc-fct-mkt: <https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/FinancialServicesEntities/> .

qta:Instrument    rdfs:seeAlso fibo-fbc-fi-fi:FinancialInstrument .
qta:assetClass    rdfs:seeAlso fibo-fbc-fi-fi:AssetClass .
qta:venue         rdfs:seeAlso fibo-fbc-fct-mkt:ExecutionVenue .
qta:tradesOn      rdfs:seeAlso <https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/tradesAt> .
```

### 5.3 검증
- [[shacl-rules]] 검증은 `rdfs:seeAlso` 를 투명하게 허용 (영향 없음)
- `scripts/check_invariants.py` TTL 파싱 테스트 통과 확인
- 초기에 "Strategy/Signal/Backtest/Incident" 는 FIBO 대응 없음을 명시적으로 주석 — 향후 확장 시 참조

---

## 6. FIBO 에서 **배울** 디자인 패턴

FIBO 를 import 하지 않더라도 그 설계 관례를 참고하면 본 프로젝트 온톨로지가 더 견고해진다.

### 6.1 IRI 네임스페이스 계층화
- FIBO: `fibo-<module>-<submodule>-<ontology>:` 명시
- 본 프로젝트: `qta:` 하나만 — 장기적으로는 `qta-strategy:`, `qta-risk:` 로 분할 고려 (볼트가 수백 노트 이상 될 때)

### 6.2 공식 레이블 + 정의 필드 강제
- FIBO 의 모든 클래스에 `rdfs:label`, `skos:definition`, `skos:example` 3필드 필수
- 본 프로젝트 `trading.ttl` 은 `rdfs:label`, `rdfs:comment` 만 존재. **SHACL 제약 추가 고려** (`skos:definition` 누락 경고)

### 6.3 Ontology Annotation (버전·릴리즈노트)
- FIBO: `owl:Ontology` 에 `owl:versionIRI`, `sm:fileAbbreviation`, `dct:issued`
- 본 프로젝트 `trading.ttl` 은 `rdfs:label`, `rdfs:comment` 만. **`owl:versionIRI` + git tag 연동 권장**

### 6.4 Restriction vs Property Shape
- FIBO 는 OWL `owl:Restriction` 사용이 많음
- 본 프로젝트는 SHACL `sh:property` 로 제약 — **동등한 표현력**, 그러나 `pyshacl` 가 OWL restriction 의 일부만 지원하는 점 주의 ([[shacl-rules]] 참고)

---

## 7. 기대 효과

| 효과 | 구현 비용 | 지속 이득 |
|------|-----------|-----------|
| 외부 데이터 소스 매핑 (예: Bloomberg FIGI, ISO 20022 메시지) 시 표준 IRI 로 연계 | 낮음 (옵션 C) | 중 |
| 금융 공시·LEI·ISIN 연결 | 중 | 중 |
| SHACL 에 FIBO 패턴 차용 (label·definition 필수) | 낮음 | 중 |
| 본 프로젝트 외 프로젝트 (예: siw-claude-template) 가 "금융 도메인" 설정 시 FIBO subset 재사용 | 중 (옵션 B) | 대 |

---

## 8. 미해결 이슈

- **KRX 특화 식별자**: FIBO 는 ISIN·CUSIP·LEI 중심. KRX 단축코드·표준코드는 FIBO 에 미포함 → 본 프로젝트에서 확장 프로퍼티 `qta:krxShortCode`, `qta:krxStandardCode` 신규 제안
- **FIBO 한국어 레이블 부재**: 모두 영문. 팀 내부는 영문 유지, 노트 본문에서 한국어 주석으로 보강
- **라이선스 경계**: FIBO Production 모듈 MIT 이나 Provisional 모듈은 Apache 2.0 또는 별도 — 선택적 import 시 라이선스 파일 검증 필요

---

## 9. 의사결정 체크리스트

- [ ] 옵션 C (alignments/fibo.ttl) 도입 — **권장 즉시**
- [ ] `owl:versionIRI` + git tag 연동 (§6.3)
- [ ] SHACL 에 `skos:definition` 필수 shape 추가 (§6.2)
- [ ] 옵션 B (SEC subset 20~30 클래스) — Phase 4+ 재평가
- [ ] KRX 특화 식별자 프로퍼티 추가 (`qta:krxShortCode`, `qta:krxStandardCode`)

---

## 관련 노트

- [[ontology-primer]] — 본 프로젝트 온톨로지 기초 설명
- [[shacl-rules]] — SHACL 제약 카탈로그 (FIBO 차용 대상)
- [[23-graphrag-for-trading-vault]] — SPARQL 레이어가 FIBO alignment 를 활용 가능
- [[14-quantum-poc-design]] — Σ 추정 맥락에서 `qta:Instrument` 가 포트폴리오 구성원
- [[19-portfolio-risk]] — 팩터 노출 회귀에서 종목 `asset_class` 활용
- [[frontmatter-guide]] — `instrument` 프론트매터의 `asset_class` 가 FIBO 와 매핑

---

## 출처

- EDM Council — *Financial Industry Business Ontology (FIBO)*. <https://spec.edmcouncil.org/fibo/>
- FIBO GitHub (edmcouncil/fibo). <https://github.com/edmcouncil/fibo>
- FIBO FND (Foundations). <https://spec.edmcouncil.org/fibo/ontology/FND/>
- FIBO SEC (Securities). <https://spec.edmcouncil.org/fibo/ontology/SEC/>
- FIBO FBC (Financial Business and Commerce). <https://spec.edmcouncil.org/fibo/ontology/FBC/>
- FIBO BE (Business Entities). <https://spec.edmcouncil.org/fibo/ontology/BE/>
- OMG — *EDMC-FIBO Specification Suite*. <https://www.omg.org/spec/EDMC-FIBO/>
- Bennett, M. (2013). *The Financial Industry Business Ontology: Best Practice for Big Data*. Journal of Banking Regulation, 14(3-4), 255–268.
- Petrova, G. et al. (2021). *Using FIBO for regulatory reporting*. Journal of Financial Compliance, 5(1).
- LEI ROC — *Legal Entity Identifier*. <https://www.leiroc.org/>
- ISO 17442-1:2020 — Financial services — Legal entity identifier (LEI). <https://www.iso.org/standard/78829.html>
