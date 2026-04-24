# 01_plan — #74 기업가치 분석 (밸류에이션) research + KIS API 재무 조회 연동

> ralplan consensus iter 2 완료 · Planner → Architect (NEEDS_MAJOR_REVISION → APPROVE_WITH_MINOR_TOUCHUPS) → Critic (ITERATE → APPROVE_WITH_POLISH_BAKED, 13 amendments baked)
> 작성: 2026-04-24

## AC 체크리스트 (이슈 본문 그대로)

### A. 신규 research 노트 `docs/background/31-valuation-analysis.md`
- [ ] 가격 지표 6종 (PER·PBR·PSR·EV/EBITDA·EV/Sales·PCR) 공식·해석·업종별 기준값
- [ ] 수익성 지표 4종 (ROE·ROA·영업이익률·부채비율)
- [ ] 성장성 지표 (매출·EPS·영업이익 성장률)
- [ ] 배당 지표 (배당수익률·배당성향·배당 성장률)
- [ ] 복합 스크리닝 조합 3종 (저평가 우량 / 성장 가치 / 고배당 안전)
- [ ] KRX 특수성 (지주사 더블카운팅·재벌 내부거래 ROE 왜곡·밸류업·별도↔연결)
- [ ] 데이터 소스 (KIS · OpenDART · pykrx)
- [ ] 출처 명시 (Damodaran·KRX 공시·교과서)

### B. KIS API 재무 데이터 조회 연동
- [ ] KIS `/uapi/domestic-stock/v1/finance/financial-ratio` 확인·문서화
- [ ] vendor raw + PIT 정규화 2계층 모듈 (`brokers/kis/fundamentals_client.py` + `data_lake/fundamentals_store.py`)
- [ ] `FUNDAMENTALS_PIT_SCHEMA` 신규 (additive), `ALL_SCHEMAS` + `partition_path` 등록

### C. 기존 노트 보강
- [ ] `13-feature-alpha-catalog` §2 에 3번째 "상세" 열 + `[[31-valuation-analysis#anchor]]`
- [ ] `20-position-sizing` §8 universe filter 관련 백링크 1줄

### D. 검증
- [ ] `scripts/check_invariants.py --strict` 통과
- [ ] 전체 `pytest` 통과 (기존 회귀 zero)

---

## 구현 계획

### A. RALPLAN-DR Summary (iter 2 최종)

#### Principles (5)
1. **팩트 소스 필수** — 모든 수치 임계값에 인라인 URL 인용 또는 "sourceless disclaimer" 명시
2. **Backward-compat** — 기존 `FACTOR_SCHEMA` 무변경, `FUNDAMENTALS_PIT_SCHEMA` **신규 추가** (additive only)
3. **Stub > live 기본값** — 기본 테스트는 `responses` 목, 라이브는 `@pytest.mark.integration`
4. **PIT 정확성 > 스키마 재사용** — announce_date / period_end 분리를 위해 별도 PIT 스키마 (factor_set 재사용안 철회)
5. **위키링크 동일 PR** — `[[31-valuation-analysis]]` 참조 시 같은 PR 안에 파일 존재

#### Decision Drivers
- **DD1**: PIT 누출 방지 ([[26-point-in-time-data]] §4)
- **DD2**: 불변식 차단 회피 (invariants #1-#3, #7)
- **DD3**: 벤더 교체 내성 (KIS → OpenDART → FnGuide 이식 시 `fundamentals_store` 재사용)

#### Finalized Decisions

| # | Decision | Why |
|---|----------|-----|
| **D1** | 모듈 2계층 분리: `src/brokers/kis/fundamentals_client.py` (vendor raw) + `src/data_lake/fundamentals_store.py` (PIT 정규화) | vendor extract와 canonical storage의 경계 유지. `src/data_lake/fundamentals.py` 는 만들지 않음 |
| **D2** | Stub + `responses` 목 + `@pytest.mark.integration` 라이브 | CI 안정성 + 팩트 근거 fixture |
| **D3** | 신규 `FUNDAMENTALS_PIT_SCHEMA(symbol, announce_date, period_end, fiscal_period, metric, value, unit, source, ingested_at)`. `ALL_SCHEMAS` 등록 + `partition_path('fundamentals', ...)` 분기. `FACTOR_SCHEMA` 재사용안 **철회**. **tz 규약: `announce_date = Asia/Seoul` (공시 기준), `ingested_at = UTC`** | PIT (announce vs period-end) + 단위 (ratio/pct/krw) + 분기성 정확한 표현 |
| **D4** | 리서치 노트 **500 라인 상한** + 모든 수치 임계값에 인라인 URL 인용 | AC 충분 이행 + 스코프 크립 방지 |
| **D5** | `13-feature-alpha-catalog.md §2` 표에 3번째 "상세" 열 추가, `[[31-valuation-analysis#N-section]]` 앵커 링크. 기존 행·KRX 주의사항 무변경 (append-only) | "상세 링크" AC 충족 + backward-compat |

#### Pre-mortem (4 scenarios)
- **S1 불변식 실패** — 위키링크 타겟 부재 → Step 2/4/14 3회 `check_invariants --strict` 게이트.
- **S2 KIS 픽스처 stale** — 공식 응답 schema 변동 → `_meta.source/captured/tr_id` 헤더 + `test_fixture_has_meta_provenance` lint.
- **S3 리서치 노트 500 라인 초과** — Step 16 `wc -l` 게이트; 초과 시 §5 스텁·§KRX 축약.
- **S4 (NEW) partition_path 분기 누락** — `FUNDAMENTALS_PIT_SCHEMA` 만 등록하고 `partition_path('fundamentals', ...)` 누락 → runtime `ValueError`. 완화: `tests/test_data_lake_schema.py::test_fundamentals_pit_registered` + `::test_partition_path_fundamentals` 단위 테스트.

#### Verification plan
- `python scripts/check_invariants.py --strict` — Step 2, 4, 14
- `pytest tests/test_data_lake_schema.py tests/test_kis_fundamentals.py -q` — Step 13
- `pytest -q` 전체 — Step 14
- Smoke: `python -c "from data_lake.schema import partition_path; print(partition_path('fundamentals', symbol='005930', ts_year=2026, ts_month=3))"` — Step 15
- `wc -l docs/background/31-valuation-analysis.md` ≤ 500 — Step 16

---

### B. ADR-lite

**Decision**: (1) 가치 평가 리서치 노트 (500-line, PIT-aware) 집필 + (2) KIS 재무비율 어댑터를 `brokers/kis/fundamentals_client.py` (vendor raw) + `data_lake/fundamentals_store.py` (PIT 정규화) 2계층 분리 + (3) 신규 `FUNDAMENTALS_PIT_SCHEMA` 를 `ALL_SCHEMAS`/`partition_path` 에 additive 등록.

**Drivers addressed**: DD1 (PIT) · DD2 (불변식) · DD3 (벤더 교체 내성)

**Alternatives considered & rejected**:
- `FACTOR_SCHEMA` 재사용 + `factor_set="kis_fundamentals_v1"` ❌ announce/period-end 이중 타임스탬프 표현 불가 → PIT 누출
- 단일 `fundamentals.py` 모듈 ❌ vendor lock-in
- 기본 live KIS 호출 테스트 ❌ CI 인증·레이트리밋 불안정

**Consequences (good/bad)**:
- ✅ `FACTOR_SCHEMA` 기반 파이프라인 무영향
- ✅ OpenDART·FnGuide 추가 시 `fundamentals_store.to_fundamentals_frame()` 확장만
- ⚠️ 스키마 2개 유지 비용 → `.ai.md` 로 완화
- ⚠️ 팩터 조인은 별도 파이프라인 필요 (#71 후속)

**Follow-ups (out of scope)**:
- 스키마 마이그레이션 툴링 · OpenDART 커넥터 · FnGuide 벤더 · #71 factor pipeline 의 fundamentals consumer · `20-sizing` universe filter 실제 코드 · 라이브 KIS 토큰 자동화 · XBRL 파싱

---

### C. Implementation Steps (17)

순서 원칙: 노트 먼저 → 불변식 게이트 → 백링크 → 불변식 게이트 → 어댑터 문서 → 픽스처 → TR-ID → 스키마 → 모듈 → 테스트 → .ai.md → 타겟 pytest → 전체 게이트 → smoke → wc 게이트 → 승인.

#### Step 1 — 리서치 노트 생성
**파일**: `docs/background/31-valuation-analysis.md`
프론트매터: `type: research, id: 31-valuation-analysis, name: "..."`.
섹션:
- §1 가격 지표 6종 (PER·PBR·PSR·EV/EBITDA·EV/Sales·PCR) — 공식·해석·Damodaran 글로벌 중앙값·업종 adjustment
- §2 수익성 지표 4종 (ROE·ROA·영업이익률·부채비율)
- §3 성장성 지표 (매출·EPS·영업이익 성장률)
- §4 배당 지표 (배당수익률·배당성향·배당 성장률)
- §5 복합 스크리닝 3종 (Magic Formula 변형 / F-Score 단순판 / 배당귀족 한국판)
- §6 KRX 특수성 (지주사 더블카운팅·재벌 내부거래 ROE 왜곡·밸류업 프로그램 2024~ 리레이팅·별도↔연결·공시 지연 60일)
- §7 데이터 소스 (KIS API / OpenDART / pykrx)
- §8 Out-of-scope (DCF · 잔여이익 · real options · FnGuide)
- §출처 — Damodaran · OpenDART · KRX 통계월보 · 학술 논문 (≥3 primary + ≥1 KRX)
**모든 수치 임계값에 인라인 URL 인용 또는 sourceless disclaimer.** Verif: S1 invariants.

#### Step 2 — 불변식 게이트 1
`python scripts/check_invariants.py --strict` → green. Verif: S1.

#### Step 3 — 카탈로그 + 사이징 백링크
- `docs/background/13-feature-alpha-catalog.md` §2 표에 **3번째 "상세" 열** 추가. Value 행 → `[[31-valuation-analysis#1-가격-지표]]`, Quality 행 → `[[31-valuation-analysis#2-수익성-지표]]`. 기존 행·KRX 주의사항 무변경.
- `docs/background/20-position-sizing.md` §8 말미에 `관련: [[31-valuation-analysis]]` (universe 필터 레퍼런스).
Verif: S1 (A4).

#### Step 4 — 불변식 게이트 2
`check_invariants --strict` → green.

#### Step 5 — 브로커 스펙 보강
**파일**: `docs/specs/broker-adapter.md`
신규 섹션 "KIS 재무비율 조회": 공식 URL, TR-ID, request params (`FID_COND_MRKT_DIV_CODE`, `FID_INPUT_ISCD`), response shape. **공식 docs URL 인용 필수.**

#### Step 6 — 픽스처 생성
**파일**: `tests/fixtures/kis/financial_ratio_sample.json`
JSON 최상위에 `_meta: { source: "<KIS docs URL>", captured: "2026-04-24", tr_id: "<TR-ID>" }` + 실제 응답 샘플 (005930 삼성전자). **공식 docs 에서 가져온 값만 사용; 필드 추측 금지.** Verif: S2 provenance lint.

#### Step 7 — TR-ID 상수
**파일**: `src/brokers/kis/tr_ids.py`
`TR_ID_FINANCIAL_RATIO = "<official id>"` + 주석 `# read-only inquiry — no paper variant (asymmetry vs order TR-IDs intentional)`. 기존 live/paper dict 패턴 유지.

#### Step 8a — 스키마 리터럴 추가
**파일**: `src/data_lake/schema.py`
```python
FUNDAMENTALS_PIT_SCHEMA: Mapping[str, str] = {
    "symbol": CAT,
    "announce_date": TS,
    "period_end": TS,
    "fiscal_period": CAT,
    "metric": CAT,
    "value": F64,
    "unit": CAT,
    "source": CAT,
    "ingested_at": TS,
}
```
tz 규약 docstring: `announce_date = Asia/Seoul`, `ingested_at = UTC`.

#### Step 8b — ALL_SCHEMAS 등록
`ALL_SCHEMAS["fundamentals"] = FUNDAMENTALS_PIT_SCHEMA` (additive).

#### Step 8c — partition_path 분기
`partition_path()` 에 `elif kind == "fundamentals": return base / "fundamentals" / f"symbol={symbol}" / f"year={ts_year}" / f"month={ts_month:02d}"` (기존 패턴 유지).

#### Step 9a — 벤더 클라이언트
**파일**: `src/brokers/kis/fundamentals_client.py::fetch_financial_ratio(symbol: str) -> FinancialRatio`
- `FinancialRatio` pydantic 모델을 `src/brokers/kis/schemas.py` 에 추가 (fields: `symbol`, `fiscal_date`, `per`, `pbr`, `eps`, `bps`, `dividend_yield`, `roe` — nullable)
- 인증·TR-ID·rate limit 은 기존 `rest.py` 재사용

#### Step 9b — 정규화 store
**파일**: `src/data_lake/fundamentals_store.py::to_fundamentals_frame(raw: FinancialRatio | list[FinancialRatio]) -> pd.DataFrame`
- 컬럼을 `FUNDAMENTALS_PIT_SCHEMA` 에 맞춰 생성
- `announce_date` vs `period_end` 분리
- `unit` 정규화 (`ratio` | `pct` | `krw`)
- `source = "kis_fin_ratio_v1"`, `ingested_at = datetime.now(timezone.utc)`

#### Step 10 — 테스트
**파일**: `tests/test_data_lake_schema.py`
- `test_fundamentals_pit_registered` — `"fundamentals" in ALL_SCHEMAS`, 9 컬럼 일치
- `test_partition_path_fundamentals` — 경로 regex 매칭

**파일**: `tests/test_kis_fundamentals.py`
- `test_fetch_financial_ratio_responses_mock` — `responses` 라이브러리로 픽스처 목
- `test_fixture_has_meta_provenance` — `_meta.source` URL regex · `_meta.captured` ISO date · `_meta.tr_id` non-empty
- `test_to_fundamentals_frame_schema_conform` — 반환 DF 컬럼이 PIT 스키마 일치
- `test_live_financial_ratio` `@pytest.mark.integration` — skipped by default

Verif: S2, S4.

#### Step 11 — `src/data_lake/.ai.md` 업데이트 (**테스트 이전**)
- 신규 `FUNDAMENTALS_PIT_SCHEMA` 섹션 + PIT 규약 (announce_date vs period_end) + tz 규약
- `fundamentals_store.to_fundamentals_frame` 소비자 계약

#### Step 12 — `src/brokers/kis/.ai.md` 업데이트 (**테스트 이전**)
- `fundamentals_client.fetch_financial_ratio` + TR-ID read-only 비대칭 기재

#### Step 13 — 타겟 pytest
`pytest tests/test_data_lake_schema.py tests/test_kis_fundamentals.py -q` → green (integration skip).

#### Step 14 — 전체 게이트
`pytest -q` 전체 + `check_invariants --strict` → green. 회귀 zero.

#### Step 15 — Smoke (선택)
`python -c "from data_lake.schema import partition_path; print(partition_path('fundamentals', symbol='005930', ts_year=2026, ts_month=3))"` → `Path(.../fundamentals/symbol=005930/year=2026/month=03)`.

#### Step 16 — 라인 게이트
`wc -l docs/background/31-valuation-analysis.md` ≤ 500. 초과 시 §5 스크리닝 축약.

#### Step 17 — 작업 내역 + 커밋 승인
`00_issue.md` 업데이트, 사용자에게 커밋 확인 (CLAUDE.md 행동 규칙).

---

### D. AC Mapping

| AC | Step | Verification |
|----|------|--------------|
| A1-A8 가격·수익성·성장성·배당·스크리닝·KRX·소스·출처 | 1 | invariants + per-threshold citation + wc ≤ 500 |
| B1 KIS 엔드포인트 확인·문서화 | 5 | docs/specs + URL |
| B2 vendor raw + PIT 정규화 모듈 | 9a + 9b | pytest 10, 13 |
| B3 PIT 스키마 + ALL_SCHEMAS + partition_path | 8a/8b/8c | test_data_lake_schema |
| C1 13-catalog §2 3번째 열 | 3 | invariants 4 |
| C2 20-sizing §8 백링크 | 3 | invariants 4 |
| D1 `check_invariants --strict` | 2, 4, 14 | green |
| D2 전체 pytest 회귀 zero | 14 | green |

---

### E. Risks & Mitigations

| # | 범주 | Risk | 완화 |
|---|------|------|------|
| R1 | Invariants | 위키링크 타겟 부재 | Step 2/4/14 3회 `check_invariants` 게이트 |
| R2 | Data quality | KIS 픽스처 stale | `_meta` provenance + `test_fixture_has_meta_provenance` |
| R3 | Scope | 노트 500 라인 초과 | Step 16 `wc -l` 게이트, §5 축약 계획 |
| R4 | CI | 라이브 KIS 호출 불안정 | `@integration` 분리, 기본 responses mock |
| R5 | **(NEW)** Schema | `partition_path` 분기 누락 | Step 8c + `test_partition_path_fundamentals` (S4) |
| R6 | **(NEW)** Module | import 경로 혼동 (`fundamentals.py` 레거시) | `.ai.md` 명시, `grep fundamentals.py` 0 hit 확인 |

---

### F. Files Created / Modified

**Created**:
- `docs/background/31-valuation-analysis.md`
- `src/brokers/kis/fundamentals_client.py`
- `src/data_lake/fundamentals_store.py`
- `tests/test_kis_fundamentals.py`
- `tests/test_data_lake_schema.py`
- `tests/fixtures/kis/financial_ratio_sample.json`

**Modified**:
- `docs/background/13-feature-alpha-catalog.md` (§2 3번째 "상세" 열 추가, append-only)
- `docs/background/20-position-sizing.md` (§8 백링크 1줄)
- `docs/specs/broker-adapter.md` (KIS 재무비율 섹션)
- `src/brokers/kis/tr_ids.py` (TR-ID + read-only 주석)
- `src/brokers/kis/schemas.py` (`FinancialRatio` pydantic)
- `src/brokers/kis/.ai.md`
- `src/data_lake/schema.py` (FUNDAMENTALS_PIT_SCHEMA + ALL_SCHEMAS + partition_path)
- `src/data_lake/.ai.md`
- `docs/work/active/000074-valuation-analysis/00_issue.md`

---

### G. Out of Scope (명시적)

- DCF / 잔여이익모형 / real-options valuation — 이론 중심 별도 이슈
- 라이브 KIS 호출 실검증 (real keys) — 별도 이슈 (integration test skeleton 만 제공)
- 밸류업 프로그램 리레이팅 백테스트 — 별도 backtest 이슈
- FnGuide 상용 벤더 연동 — 별도 이슈
- #71 factor pipeline 에 fundamentals consumer 통합 — 별도 이슈
- `20-position-sizing` universe filter 실제 코드 — 별도 이슈 (본 PR = 텍스트 참조만)
- Dataview 대시보드 (밸류에이션 스크리닝) — 별도 이슈
- XBRL 파싱 / DART 공시 원문 — 별도 이슈

---

### H. 승인

**Critic 최종 verdict: APPROVE_WITH_POLISH_BAKED**
- Architect 6 amendment + Critic 5 bake-in + tz 규약 + test 이름 = **13 amendment 전부 반영**
- 5 Principles 유지, 3 Drivers 모두 충족, 4 Pre-mortem 시나리오 → test 연결 완료

**다음**: Step 1 부터 순차 실행.
