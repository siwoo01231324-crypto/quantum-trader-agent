---
type: work-done
id: 00_issue
name: "#62 리서치 스프린트 3 — PIT · Corporate Actions · Paper-to-Live · Market Regime"
status: done
---

# chore: 리서치 스프린트 3 — PIT · Corporate Actions · Paper-to-Live · Market Regime

## 관련 노트 (구현 대상)

- [[26-point-in-time-data]]
- [[27-corporate-actions]]
- [[29-paper-to-live-protocol]]
- [[30-market-regime-detection]]

## 목적

데이터 품질·운영 성숙도 영역 research 보강. #60·#61 의 "볼트 사전조회 필수" 규칙 적용 결과 원래 5개 중 **28 (KR alt-data catalog) 은 `13-feature-alpha-catalog` 가 이미 7종 카탈로그·접근 경로 모두 커버.** 스코프 4개로 축소.

## 완료 기준

### A. 신규 research 4개
- [x] [[26-point-in-time-data]] — PIT snapshot 설계, 수정주가 (backward adjust) 공식, 상장폐지 처리, 생존편향 방어
- [x] [[27-corporate-actions]] — 이벤트별 가격·수량 조정 규칙 (split·merge·dividend·rights·merger·spinoff), `corp_action` 테이블 확장안
- [x] [[29-paper-to-live-protocol]] — Shadow → Live Paper → Pilot (5%) → Full Production 4단계 + Exit Criteria + 롤백 트리거
- [x] [[30-market-regime-detection]] — HMM·Markov Switching·Rule-based 분류, Vol × Trend-Range 매트릭스, 전략 스위칭 규칙

### B. 기존 노트 보강
- [x] 28 은 별도 노트 대신 기존 `13-feature-alpha-catalog` 의 커버리지 재확인 + `[[26]]·[[27]]·[[30]]` 역참조만 추가

### C. 위키링크 백필 + 검증
- [x] 각 신규 research 에 관련 `[[id]]` 7개 이상
- [x] `data-lake-schema` · `12-validation-protocol` · `kill-switch-runbook` · `13-feature-alpha-catalog` · `19-portfolio-risk` 에 역참조 섹션 확장
- [x] `scripts/check_invariants.py --strict` 통과 (78 노트)
- [x] `scripts/ontology_sync.py --write` (4 인스턴스 유지)
- [x] 각 노트 하단 출처 (KRX·DART·학술 논문·NautilusTrader·RiskMetrics 등) 명시

## 작업 내역

### 2026-04-17

**1. 사전조회 (CLAUDE.md 규칙 적용)**

규칙 적용 사이클 3번째. 5개 주제 중:
- **26 (PIT·survivorship)**: `data-lake-schema` 에 `delisted_at` 컬럼만 있고 설계 세부 없음. **진짜 gap**
- **27 (corporate actions)**: `data-lake-schema` §4.5 에 `corp_action` DDL 만, 처리 로직 전무. **진짜 gap**
- **28 (KR alt-data catalog)**: `13-feature-alpha-catalog` §3 이 이미 7종 (공매도·뉴스감성·체결강도·검색량·수급·DART·신용잔고) + §4 접근 경로 (KRX OpenAPI·pykrx·OpenDART·공공데이터포털) 커버. **스코프 제외**
- **29 (paper-to-live)**: `12-validation-protocol` §3.8 + `kill-switch-runbook` 체크리스트 한 줄씩. 프로토콜 상세 없음. **진짜 gap**
- **30 (regime detection)**: `08-strategy-paradigms`·`19-portfolio-risk` 개념 언급만. 탐지 방법론 없음. **진짜 gap**

**2. 신규 research 4건**

- `26-point-in-time-data.md` — Bitemporal vs Append-only vs Time Travel 3가지 아키텍처, backward adjustment 공식 체계화, 상장폐지 6 이벤트 생명주기, `tradable_universe()` API 제안, T1~T5 PIT 불변식 CI 테스트 제안, `ohlcv_adj` 물리화 확장
- `27-corporate-actions.md` — 8 이벤트 카테고리 (split·merge·cash_dividend·stock_dividend·bonus·rights·merger·spinoff) 각 조정 공식, data-lake-schema `corp_action` 테이블 필드 확장 제안, 백테스트 의사코드, 데이터 소스 4종 (KIND·DART·pykrx·증권사) 교차검증 전략, 테스트 케이스 6개 (삼성 2018·LG화학 2020 등 실 사례)
- `29-paper-to-live-protocol.md` — 4-Phase 프레임워크 (Shadow Paper → Live Paper → Live Pilot → Full Production), 각 단계 Exit Criteria + 롤백 트리거 수치 기준, DSR 역치 단계별 조정표 (N 크기 기반), Full Production 스케일업 5 마일스톤 (M1~M5), 스위칭 hysteresis 규칙
- `30-market-regime-detection.md` — 4축 체제 정의 (Vol·Trend·Risk-On/Off·Bull/Bear), 알고리즘 3 계보 (HMM·Markov Switching Regression·Rule-based), Vol × Trend 2×3 의사결정 매트릭스, hysteresis + 점진적 사이징 스위칭 규칙, 한국 시장 특이점 5개 (VKOSPI 유동성·공매도 금지·개인 비중·동시호가·규제 이벤트)

**3. 역참조 백필**
- `data-lake-schema` → `[[26]]` · `[[27]]`
- `12-validation-protocol` → `[[26]]` · `[[27]]` · `[[29]]` · `[[30]]`
- `13-feature-alpha-catalog` → `[[26]]` · `[[27]]` · `[[30]]`
- `19-portfolio-risk` → `[[30]]` · `[[26]]`
- `kill-switch-runbook` → `[[29]]` · `[[30]]`

**4. 검증**
- `check_invariants --strict`: 78 노트 통과
- `ontology_sync --write`: 4 인스턴스 유지 (research 는 RDF 인스턴스화 대상 아님)

**5. 스코프 재정의 효과 (#60 → #61 → #62 추세)**
- #60: 실제 작업 도중 중복 발견 → 재정의 (1 사이클 손실)
- #61: 시작 전 재정의 (22 스킵, 24 재정의) — 규칙 적용 첫 성공
- #62: 시작 전 재정의 (28 스킵) — 규칙이 자연스러운 업무 흐름으로 정착
- 3 사이클 만에 "사전조회 필수" 규칙이 작업 낭비를 0 에 수렴시킴
