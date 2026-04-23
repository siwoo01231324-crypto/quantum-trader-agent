# chore: 타 AI/자동매매 특허 리서치 — 시스템 강화 + 회피설계 근거

## 배경
- #69 포지션 사이징 PR #77 머지 대기 중. 실데이터 검증 과정에서 경쟁 특허(업리치 KR 10-2024-0114873, 공개 KR 10-2026-0030316) 를 발견하고 청구항 1·8 을 분석.
- 업리치 특허 분석에서 5 가지 설계 인사이트 추출:
  1. **사용자 리스크 프로필** (R1 = f(매매성향·포트폴리오·부채·상환비율))
  2. **복합 리스크 지수** (R2 = 소셜 + 가격 + 거시경제)
  3. **BTC dominance 레짐 스위치** (C = a × Bdominance × T)
  4. **알트코인 안정성 등급** (A~F 시총·유통량·온체인 기반)
  5. **우리 차별점** — 검증 프로토콜·PIT·실행 알고·kill-switch·세무 (업리치에 부재)
- 업리치 한 건만으로는 관점 편향. 포트폴리오 최적화·팩터 조합·주문 실행 3 개 주제에서 KIPRIS/USPTO/EPO 대표 특허 3~5 개씩 조사 필요.

## 목적
1. **남의 특허를 보고 우리 시스템 강화** — 차용 가능한 개념·수식·아키텍처 추출.
2. **회피해야 할 독점 영역 명시** — 청구항 구성요소 전체 복제 금지, 설계 단계 침해 리스크 제거.
3. 조사 결과를 `docs/background/` 리서치 노트로 장기 자산화 (RDF 온톨로지 동기 포함).

## 범위

### 리서치 노트 4 개 작성
- `docs/background/31-uprich-patent-analysis.md` — 업리치 KR 10-2024-0114873 청구항 1·8 해부, 우리 시스템 대응 매트릭스, 5 가지 차용/회피 포인트, 후속 이슈 후보.
- `docs/background/32-patents-portfolio-optimization.md` — CVaR·risk parity·HRP·상관매트릭스 KR/US/EP 특허 3~5 건 (#70 포트폴리오 리스크 이슈 입력).
- `docs/background/33-patents-factor-models.md` — 알파 팩터·신호 조합·ML 기반 시그널 특허 3~5 건 (#71 알파 팩터 파이프라인 입력).
- `docs/background/34-patents-execution-algos.md` — TWAP/VWAP/SOR/마이크로구조·HFT 실행 특허 3~5 건 (향후 실행 알고 이슈 참고).

### 각 노트 필수 섹션
- 특허별 **청구항 핵심 요약** (독립항 + 핵심 종속항)
- **차용 가능 개념** — 우리 아키텍처에 이식 가능한 부분
- **회피 필요 영역** — 청구항 전체 복제 금지·대체 설계 제안
- **우리 코드 연결고리** — `src/risk/`, `src/backtest/`, `src/execution/` 등 매핑
- **출처** — 출원번호, 공개번호, 공개일, 출원인, URL (KIPRIS/Google Patents)

### 법적 주의 (본 이슈 공통 불변식)
- 청구항 1 의 **전체 구성요소**를 한 시스템에 그대로 구현 금지.
- 수식 그대로 복제 금지. 동등 결과라도 **다른 정의·다른 파라미터** 로 재설계.
- 본 노트들은 **연구·회피설계 목적** 임을 모든 노트 상단에 명시.

## 완료 기준
- [x] `31-uprich-patent-analysis.md` 작성 + 후속 이슈 후보 최소 2 개 도출.
- [x] `32-patents-portfolio-optimization.md` 특허 3~5 건 조사 완료.
- [x] `33-patents-factor-models.md` 특허 3~5 건 조사 완료.
- [x] `34-patents-execution-algos.md` 특허 3~5 건 조사 완료.
- [x] 모든 노트 프론트매터 `type: research` + `id` 일치, 본문 위키링크 검증.
- [x] `scripts/check_invariants.py --strict` 통과.
- [x] 각 노트 하단 `## 출처` 에 KIPRIS/Google Patents URL 포함.
- [x] 도출된 '차용 후보' 항목을 신규 백로그 이슈로 **최소 2 개 제안** (본 이슈에서 링크).

## 선행 조건
- #69 (포지션 사이징) 머지 — 본 조사의 출발점이 #69 실데이터 관찰이므로 컨텍스트 확정 후 진행 권장.

## 관련
- `docs/background/20-position-sizing.md` (포지션 사이징 이론)
- `docs/background/30-market-regime-detection.md` (BTC dominance 레짐과 연결)
- `docs/background/12-validation-protocol.md` (walk-forward — 우리 차별점 근거)
- 업리치 특허 KIPRIS 링크: https://kpat.kipris.or.kr/kpat/biblioa.do?method=biblioFrame&applno=1020240114873

## 작업 내역

### 2026-04-24 (team-exec 완료)

**현황**: 8/8 완료
**완료된 항목**:
- `31-uprich-patent-analysis.md` 작성 — 업리치 KR 10-2024-0114873 청구항 1·8 해부, 강화 제안 5개, 후속 이슈 후보 2개
- `32-patents-portfolio-optimization.md` 작성 — CVaR·ERC·HRP·PRI 관련 US/KR 특허 5건, 강화 제안 5개, 후속 이슈 후보 2개
- `33-patents-factor-models.md` 작성 — 팩터 모델·알파 시그널 US 특허 4건, 강화 제안 4개, 후속 이슈 후보 2개
- `34-patents-execution-algos.md` 작성 — TWAP·VWAP·SOR·IS 실행 US 특허 4건, 강화 제안 4개, 후속 이슈 후보 2개
- 모든 노트 프론트매터 `type: research`, `id` 파일명 일치 확인
- 본문 위키링크 13개 전체 존재 확인 (broken link 없음)
- `scripts/check_invariants.py --strict` 통과 (89 노트 검증)
- 각 노트 `## 출처` 에 Google Patents / KIPRIS URL 포함
- 차용 후보 후속 이슈 8개 도출 (각 노트 §후속 이슈 후보 섹션)
**변경 파일**: 4개 신규 research 노트 생성, `00_issue.md` 업데이트
**강화 제안 총계**: 18개 (31: 5개, 32: 5개, 33: 4개, 34: 4개)

### 2026-04-24

**현황**: 0/8 완료 (플랜 수립 단계)
**완료된 항목**:
- (없음)
**미완료 항목**:
- `31-uprich-patent-analysis.md` 작성 + 후속 이슈 후보 ≥ 2 개
- `32-patents-portfolio-optimization.md` 특허 3~5 건
- `33-patents-factor-models.md` 특허 3~5 건
- `34-patents-execution-algos.md` 특허 3~5 건
- 프론트매터 `type: research` + `id` 일치, 위키링크 무결성
- `scripts/check_invariants.py --strict` 통과
- 각 노트 `## 출처` 에 KIPRIS/Google Patents URL
- 차용 후보 신규 백로그 이슈 ≥ 2 개 제안
**변경 파일**: `01_plan.md` (구현 계획 구체화), `00_issue.md` (작업 내역 스냅샷)
**다음 단계**: `docs/background/31-uprich-patent-analysis.md` 초안 작성 — 업리치 청구항 1·8 + 5 인사이트 형식화

## 후속 이슈 후보

본 리서치에서 도출된 우선 차용 후보. 실제 이슈 생성은 사용자 승인 후.

1. **알트코인 안정성 등급 필터 (StabilityGrade A~F) 구현** — 노트: `[[31-uprich-patent-analysis]]` / 근거: 업리치 특허 청구항 1-(d) 알트코인 안정성 등급 개념 차용, 입력변수·등급기준 자체 재정의 / 연결 코드: `src/universe/stability_grade.py` (신규)

2. **BTC dominance 레짐 팩터 구현** — 노트: `[[31-uprich-patent-analysis]]`, `[[30-market-regime-detection]]` / 근거: 업리치 특허 청구항 1-(c) BTC dominance 추세 지표 개념 차용, 이진 신호로 단순화하여 회피 / 연결 코드: `src/factors/btc_dominance.py` (신규)

3. **다중 CVaR 계층 경보 시스템** — 노트: `[[32-patents-portfolio-optimization]]` / 근거: Axioma US20210110479A1 계층적 CVaR 구조 차용, GUI 없이 백엔드만 구현 / 연결 코드: `src/risk/portfolio_orchestrator.py`

4. **CVaR 위반 시 점진적 포지션 감축 루프** — 노트: `[[32-patents-portfolio-optimization]]` / 근거: AIG US10664914B2 반복적 제약 강화 패턴 차용, CVaR 계산과 반복 루프를 별도 모듈로 분리 / 연결 코드: `src/risk/portfolio_orchestrator.py`

5. **팩터 파이프라인 품질 게이트 (ICIR 필터 + 상관 중복 제거)** — 노트: `[[33-patents-factor-models]]` / 근거: Axioma US20130332391A1 순차 최적화 구조 차용, 2-리스크모델 대신 단일 모델+동적 가중치로 회피 / 연결 코드: `src/signals/pipeline.py` (신규)

6. **팩터 직교화 정규화 모듈 (orthogonal_neutralize)** — 노트: `[[33-patents-factor-models]]` / 근거: Axioma US20140081889A1 포기 특허 Gram-Schmidt 직교화 차용, 자동 변환 대신 사전 전처리로 분리 / 연결 코드: `src/signals/neutralize.py` (신규)

7. **TWAP·VWAP KRX VI/circuit-breaker 실행 게이트** — 노트: `[[34-patents-execution-algos]]` / 근거: Ginis US20210272201A1 볼라틸리티 레짐 적응 개념 차용, ML 없는 규칙 기반으로 회피 / 연결 코드: `src/execution/twap.py`, `src/execution/krx_handler.py`

8. **Implementation Shortfall 사전 추정 + 실행 TCA 메트릭** — 노트: `[[34-patents-execution-algos]]` / 근거: BlackRock US12067619B1 IS 기반 라우팅 선택 개념 차용, 라우팅 로직 아닌 측정·로깅만 채택 / 연결 코드: `src/brokers/router.py`, `src/observability/`

→ 독립 7개는 신규 이슈 #87
