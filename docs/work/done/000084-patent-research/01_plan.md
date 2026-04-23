# 01_plan — 타 AI/자동매매 특허 리서치

> ⚠️ 이 파일은 `/start-issue` 가 생성한 **AC 체크리스트 초안**이다.
> 구현 시작 전 `/plan` 커맨드로 구체적 구현 계획(조사 범위·검색 쿼리·노트 템플릿·회피 체크리스트)을 덧붙여야 한다.

## Acceptance Criteria

- [ ] `docs/background/31-uprich-patent-analysis.md` 작성 + 후속 이슈 후보 최소 2 개 도출
- [ ] `docs/background/32-patents-portfolio-optimization.md` 특허 3~5 건 조사
- [ ] `docs/background/33-patents-factor-models.md` 특허 3~5 건 조사
- [ ] `docs/background/34-patents-execution-algos.md` 특허 3~5 건 조사
- [ ] 모든 노트 프론트매터 `type: research` + `id` 일치, 위키링크 무결성
- [ ] `scripts/check_invariants.py --strict` 통과
- [ ] 각 노트 하단 `## 출처` 에 KIPRIS/Google Patents URL 포함
- [ ] 도출된 '차용 후보' 항목을 신규 백로그 이슈로 최소 2 개 제안 (본 이슈에서 링크)

## 구현 계획

> 작성: 2026-04-24

### 북극성 (North Star) — 조사의 1 순위 산출물

**본 조사의 최우선 목적은 "남의 특허에서 우리 프로젝트에 가져올 만한 것을 찾아 시스템을 강화" 하는 것이다.**
회피 설계·법적 리스크 체크는 필수 부산물이지만 부차적이다.

각 노트의 성공 기준은 **"우리 레포에 적용 가능한 구체적 강화 제안(Strengthening Proposal)"** 이 ≥ 1 개 산출되는 것이다. 예:

- "이 특허의 구성요소 X 를 우리 `src/risk/portfolio_orchestrator.py` 의 Y 함수에 이런 모양으로 접목 가능 — 기존 Z 대비 장점: ..."
- 추상적 "아이디어 재미있음" 수준은 미달. 최소한 **어떤 파일 / 어떤 함수 / 어떤 파라미터** 에 어떻게 붙일지 한 문단으로 서술.

제안 집계 목표:
| 노트 | 강화 제안 최소 개수 |
|------|---------------------|
| 31 업리치 | 3 개 이상 (이미 5 인사이트 확보) |
| 32 포트폴리오 | 2 개 이상 |
| 33 팩터 | 2 개 이상 |
| 34 실행 | 1 개 이상 (향후 이슈 입력용) |
| **합계** | **≥ 8 개** |

모든 강화 제안은 `00_issue.md` 하단 "후속 이슈 후보" 섹션에 집계한다. 그 중 ≥ 2 개는 실제 백로그 이슈 제안으로 승격 (AC-8).

### 0. 작업 순서 (순차)

1. **업리치 특허 분석 노트** (`31-uprich-patent-analysis.md`) — 이슈 body 에 이미 정리된 5 가지 인사이트를 정식 리서치 노트로 형식화.
2. **포트폴리오 최적화 특허 조사** (`32-patents-portfolio-optimization.md`) — #70 대응.
3. **팩터·알파 특허 조사** (`33-patents-factor-models.md`) — #71 대응.
4. **실행 알고리즘 특허 조사** (`34-patents-execution-algos.md`) — 향후 실행 이슈 입력.
5. **무결성 검증** (`check_invariants.py --strict`) → `00_issue.md` AC 업데이트 → 후속 이슈 ≥ 2 개 제안 기록.
6. 최종 `git status` 확인 후 사용자 승인 아래 커밋·PR.

각 단계는 다음 단계 시작 전에 단독 커밋 가능한 상태로 마무리한다 (검증 실패 시 범위 축소 쉽게 하기 위함).

### 1. 생성 파일 목록

| 단계 | 경로 | 비고 |
|------|------|------|
| 1 | `docs/background/31-uprich-patent-analysis.md` | 신규 research 노트 |
| 2 | `docs/background/32-patents-portfolio-optimization.md` | 신규 research 노트, 특허 3~5 건 |
| 3 | `docs/background/33-patents-factor-models.md` | 신규 research 노트, 특허 3~5 건 |
| 4 | `docs/background/34-patents-execution-algos.md` | 신규 research 노트, 특허 3~5 건 |
| 5 | `docs/work/active/000084-patent-research/00_issue.md` | AC 체크·후속 이슈 후보 기록 |
| 5 | `docs/work/active/000084-patent-research/01_plan.md` | 본 파일 — 진행 체크 업데이트 |

### 2. 볼트 사전조회 (CLAUDE.md 규칙, 신규 노트 전 필수)

각 노트 작성 **직전** 에 다음을 수행:

```bash
grep -ri "<주제 키워드>" docs/background/ docs/specs/
```

- 포트폴리오 최적화 → `[[19-portfolio-risk]]`, `[[20-position-sizing]]` 커버 범위 확인
- 팩터 모델 → `[[13-feature-alpha-catalog]]`, `[[08-strategy-paradigms]]`
- 실행 알고 → `[[07-market-microstructure-basics]]`, `[[10-broker-api-comparison]]`
- 업리치 인사이트 → `[[30-market-regime-detection]]` (BTC dominance), `[[12-validation-protocol]]` (우리 차별점)

중복 발견 시 **신규 노트 대신 해당 노트에 섹션 추가** 로 방향 전환.

### 3. 특허 검색 쿼리 템플릿

**KIPRIS (한국)** — `https://kpat.kipris.or.kr`
- 포트폴리오: `"포트폴리오 최적화" OR "리스크 패리티" OR "CVaR" OR "HRP"` · IPC G06Q 40
- 팩터: `"알파 팩터" OR "신호 조합" OR "퀀트 팩터" OR "앙상블 시그널"`
- 실행: `"주문 실행" OR "TWAP" OR "VWAP" OR "스마트 라우팅"`
- 업리치 직접 조회: `applno=1020240114873`

**Google Patents** — `https://patents.google.com`
- `site:patents.google.com (portfolio optimization) (CVaR OR "risk parity") after:2018`
- CPC: `G06Q40/04` (거래), `G06N20/00` (ML)
- 우선권·패밀리 정보로 원출원 추적

**USPTO** — `https://ppubs.uspto.gov/pubwebapp/`
- CPC 분류 `G06Q 40/04`, `G06Q 40/06` 필터링

**EPO Espacenet** — `https://worldwide.espacenet.com`
- Smart search: `CPC=G06Q40/04 AND txt="risk parity"`

수집 후 각 특허는 **출원번호, 공개번호, 공개일, 출원인, URL ≥ 1** 을 기록한다.

### 4. 각 노트 표준 섹션 구조

```markdown
---
type: research
id: {31|32|33|34}-{short}
name: "제목"
sources:
  - {KIPRIS 또는 Google Patents URL 1}
  - {URL 2}
  - ...
---

# 제목

> ⚠️ **법적 고지**: 본 노트는 학술·회피설계 목적 조사이며 변리사 리뷰가 아님.
> 상용 서비스 전 법무 검토 필수.
> 관련 노트: [[id1]], [[id2]] — 우리 시스템 강화 및 침해 리스크 제거 목적.

## 1. 조사 범위

## 2. 특허 N — {출원번호 / 공개번호}
### 2.1 서지 정보 (출원인·공개일·법적 상태)
### 2.2 청구항 핵심 요약 (독립항 + 주요 종속항, 발췌·요약)
### 2.3 💎 강화 제안 (Strengthening Proposal) — **본 조사의 핵심 산출물**
   - 제안 이름: (한 줄)
   - 적용 대상 파일/함수: `src/…/foo.py::bar()` 또는 신규 모듈 경로
   - 접목 방법: 기존 구조 대비 어디에 어떤 파라미터·데이터 흐름으로 붙일지 1~2 문단
   - 기대 효과: 어떤 메트릭·UX·안전성이 개선되는가 (정성·정량)
   - 저비용 검증 경로: 프로토타입·실험 제안 (옵션)
### 2.4 차용 아이디어 메모 (아직 설계 수준 미달이지만 기록할 가치 있는 것)
### 2.5 회피 필요 영역 — 구성요소 카탈로그 (a/b/c/…) + 대체 설계
### 2.6 우리 코드 연결고리 (`src/…` 파일·모듈 링크 + 현재 상태 요약)

## N. 종합 매트릭스 (특허 × 강화제안 / 회피)
   - 표: 특허 | 강화 제안 요약 | 회피 포인트 | 연결 코드 경로

## N+1. 우리 레포 강화 로드맵 (본 노트 집계)
   - 본 노트에서 도출한 모든 강화 제안을 한 표로 집계
   - 우선순위(High/Med/Low) + 예상 난이도 + 의존 이슈 태깅

## N+2. 후속 이슈 후보 (해당 시)

## 출처
- KIPRIS: ...
- Google Patents: ...
```

### 5. AC 별 Task Flow

**AC-1 — `31-uprich-patent-analysis.md`**
- 업리치 KR 10-2024-0114873 (공개 10-2026-0030316) 공개공보 본문 확인 (KIPRIS applno URL).
- 청구항 1·8 원문 발췌·요약 (전문 복제 금지, 필요 구성요소만 카탈로그화).
- 5 인사이트별 섹션:
  - R1 사용자 리스크 프로필 → `src/risk/position_sizer.py` (#69) 연결
  - R2 복합 리스크 지수 → `docs/specs/strategies/*.md` risk 메타데이터 확장 여지
  - BTC dominance 레짐 스위치 → `[[30-market-regime-detection]]` 연결
  - 알트코인 안정성 등급(A~F) → 신규 모듈 후속 이슈 후보
  - 우리 차별점(검증 프로토콜·PIT·실행·kill-switch·세무) → `[[12-validation-protocol]]`, `[[26-point-in-time-data]]` 연결
- 각 인사이트에 **회피 설계** 서브섹션 작성: 청구항 구성요소 어느 하나를 의도적으로 생략 또는 대체 개념으로 치환.
- 후속 이슈 후보 ≥ 2 개 명시 (예: "알트코인 안정성 등급 분류기 설계", "복합 리스크 지수 R2 스펙 제안").

**AC-2 — `32-patents-portfolio-optimization.md`**
- 키워드: CVaR, risk parity, HRP, ERC, Black-Litterman, DCC-GARCH, 상관 클러스터링
- KIPRIS + Google Patents 에서 3~5 건 선별 (검증 포인트: 독립항 명확, 시장 관련성, 최근 10 년 이내 공개 우선)
- `[[19-portfolio-risk]]` · `[[20-position-sizing]]` 와 매핑, `src/risk/portfolio_orchestrator.py` (#70) 코드 연결고리 명시
- 종합 매트릭스: 특허 × (차용 / 회피) 표

**AC-3 — `33-patents-factor-models.md`**
- 키워드: 알파 팩터, 팩터 조합, ML 기반 시그널, 앙상블, 스태킹, 팩터 IC, 룩어헤드 방지
- `[[13-feature-alpha-catalog]]`, `[[08-strategy-paradigms]]` 연결
- `src/factors/` (#71 알파 팩터 파이프라인) 매핑

**AC-4 — `34-patents-execution-algos.md`**
- 키워드: TWAP/VWAP, SOR(Smart Order Routing), 마이크로구조, HFT, 슬리피지 예측
- `[[07-market-microstructure-basics]]`, `[[10-broker-api-comparison]]` 연결
- `src/execution/` (향후 이슈) 와 연결 예정

**AC-5 — 프론트매터 + 위키링크 무결성**
- `type: research`, `id` = 파일명(확장자 제외), `name` · `sources` 필수
- 본문 `[[id]]` 위키링크 대상이 `docs/**/*.md` 에 실제 존재하는지 작성 중 실시간 검증
- 인라인/펜스드 코드 블록 안의 `[[…]]` 는 스캐너 제외 대상 — 안전

**AC-6 — 불변식 통과**
- 실행: `python scripts/check_invariants.py --strict`
- 실패 메시지 기반 수정 후 재실행. 반복 가능한 검증 루프.

**AC-7 — 출처 URL**
- 각 노트 하단 `## 출처` 섹션에 KIPRIS applno URL 및 Google Patents 링크 포함
- 프론트매터 `sources` 와 중복 가능 (본문은 읽기용, 프론트매터는 RDF 동기화용)

**AC-8 — 후속 이슈 후보 ≥ 2 개**
- `00_issue.md` 하단에 "후속 이슈 후보" 섹션 추가 → 제목·요약·연결 노트·근거 청구항 기록
- 실제 `gh issue create` 는 **사용자 승인 후** 실행 (자동 생성 금지)

### 6. 회피설계 체크리스트 (각 특허마다)

- [ ] 청구항 1 구성요소를 카탈로그화 (a / b / c / …)
- [ ] 우리 시스템이 **전체 구성요소를 한 모듈에서** 구현하고 있는가? → "아니오" 확인
- [ ] 수식 그대로 복제 없음 — 동등 목적이라도 **다른 정의 · 다른 파라미터**
- [ ] 구성요소 중 **하나를 의도적으로 생략** 또는 **대체 개념으로 치환**
- [ ] 대체 설계 아이디어 명시 (1 줄 이상)

### 7. Guardrails

**Must Have**
- 각 특허: 출원번호 / 공개번호 명시 + URL ≥ 1
- **각 특허마다 "💎 강화 제안" 섹션을 반드시 채울 것** — 빈 섹션 금지. 제안 없으면 그 특허를 조사 대상에서 드롭하고 다른 특허로 대체.
- **강화 제안 집계**: 31≥3, 32≥2, 33≥2, 34≥1 (합계 ≥ 8 개). 미달 시 조사 범위 확장.
- 각 노트 하단 "강화 로드맵" 표 (제안 × 우선순위 × 연결 코드)
- 각 노트 상단 법적 고지 문구 ("본 노트는 학술·회피설계 목적, 변리사 리뷰 아님")
- 팩트 기반 기술. 추측·의견은 별도 "평가" 서브섹션으로 격리.
- 프론트매터 `type: research`, `id` 파일명 일치
- `[[id]]` 위키링크 대상이 실제 존재

**Must NOT Have**
- 청구항 원문 과다 복제 (발췌·요약만)
- 바이너리 첨부 (PDF·CSV·parquet — 레포 규칙 위반)
- LLM 추정만으로 출원번호·청구항 생성 — 반드시 실제 공개공보 확인
- 자동 `gh issue create` — 후속 이슈는 *제안* 만, 생성은 사용자 승인 뒤
- `.draft.md` 로 커밋 (정식 research 노트 바로 생성)
- **추상적 "재미있는 아이디어" 수준의 강화 제안** — 파일/함수/파라미터 중 최소 하나는 특정해야 함
- **회피 설계 섹션만 채우고 강화 제안을 부실하게 남기기** — 북극성 위반

### 8. 검증 단계

1. 각 노트 작성 직후 → `python scripts/check_invariants.py --strict`
2. 위키링크 체크:
   ```bash
   grep -oE '\[\[[a-z0-9-]+\]\]' docs/background/3{1,2,3,4}-*.md | sort -u
   ```
   각 타겟이 `docs/**/*.md` 파일로 존재하는지 확인.
3. 프론트매터 `id` ↔ 파일명 일치 시각 확인.
4. `00_issue.md` AC 체크리스트 `- [ ]` → `- [x]` 전환.
5. 최종 `git status` + `git diff` 리뷰 후 **사용자에게 커밋 승인 요청**.

## 리스크·오픈 퀘스천

- **침해 판단의 한계**: 본 조사는 변리사 리뷰 아님. 상용 서비스 직전엔 반드시 법적 검토 필요 — 노트 상단 고지.
- **특허 접근성**: KIPRIS 검색 UI 가 SPA 라 자동화 어려움. Google Patents + 수동 URL 조합으로 대체.
- **노트 개수 상한**: 주제당 5 건 초과 시 관리 부담. 3~5 건 기준 준수.
- **후속 이슈 필요성**: 도출된 차용 후보가 실제로 가치 있는지, 또는 이미 우리 리서치(#20·#30·#12) 에 커버되는지 중복 체크 필요.

## 선행 조건

- #69 포지션 사이징 머지 권장 (본 조사의 출발점)

## 참고

- `docs/background/20-position-sizing.md`
- `docs/background/30-market-regime-detection.md`
- `docs/background/12-validation-protocol.md`
- 업리치 특허 출원번호 10-2024-0114873 (공개 10-2026-0030316, 법적상태 공개)
- KIPRIS: https://kpat.kipris.or.kr/kpat/biblioa.do?method=biblioFrame&applno=1020240114873
