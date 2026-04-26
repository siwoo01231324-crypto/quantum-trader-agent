# chore: 엔드투엔드 프로젝트 기획서 작성 (docs/whitepaper/qta-master-plan-v01.md)

## 선행 조건
- **#85** (메타라벨링 레이어) **머지 후 진행**. Whitepaper 섹션 4(피처·신호) / 5(전략) / 11(진척도) 는 메타라벨러 아키텍처가 확정되어야 정확히 기술 가능.

---

## 목적
프로젝트 전체를 **엔드투엔드 단일 문서**로 명시화한 v0.1 마스터 기획서를 `docs/whitepaper/` 에 작성한다. "우리가 뭘 리서치했고, 어떤 지표·전략·리스크·사이징·배포로 실거래까지 갈 것인가" 를 외부인도 읽고 재현할 수 있을 만큼 구체적으로.

## 배경
- `docs/background/` 에 30여 개 리서치 노트가 있으나 흩어져 있어 **전체 흐름** 파악 불가
- `docs/specs/` 는 개별 기능 스펙만 — 시스템 전체의 End-to-End 설계가 한눈에 안 보임
- 어떤 이슈(#19/20/24/25/26/27/47/51/53/67/68/69/70/71) 가 어느 최종 산출물에 기여하는지 추적 어려움
- 실거래·EXE 배포·매매 단위 등 구현 후반부는 여태 **구체 수치 없음**
- 현재 어디까지 됐는지 (구현 vs 로드맵) 한 장짜리 진척도 대시보드 필요

## 완료 기준
- [x] `docs/whitepaper/qta-master-plan-v01.md` 신규 작성. 프론트매터 스키마 (`type: whitepaper`, `id: qta-master-plan-v01`, `name`, `version: \"0.1\"`) 준수
- [x] 아래 11개 섹션 전부 채움 — 각 섹션은 **기존 노트 위키링크** 와 **구체 수치/파라미터** 둘 다 포함
- [x] "구현 진척도" 섹션이 머지된 이슈(#19/20/24/…)·현재 브랜치·열린 이슈를 표로 정리해 한눈에 보이게
- [x] 위키링크 대상 노트 전원 존재 (CI `check_invariants.py --strict` 통과 — 113 노트 검증)
- [x] `AGENTS.md` 의 '핵심 문서 링크' 섹션에 마스터 기획서 항목 추가

## 구현 플랜

### 작성할 11개 섹션 골격

1. **Executive Summary** — 1페이지. 한국 개인 투자자용 LFT 규칙기반 퀀트 에이전트라는 정체성, 목표 Sharpe·MDD·연수익, 완성 시점 목표.
2. **리서치 근거** — `docs/background/01~30` 를 주제별 그룹(시장미시구조·전략패러다임·피처카탈로그·PIT/CA·검증·포트폴리오리스크·페이퍼투라이브·체제탐지·LLM가드레일·GraphRAG·FIBO)으로 요약 + 각 노트 위키링크.
3. **시장·종목 universe** — 암호화폐(BTCUSDT)·KRX 중대형주·필터(ADV/유동성) 기준 수치 명시. 공매도 금지구간·밸류업 인덱스 등 구간 처리.
4. **피처·신호 명세** — 구현 완료된 팩터(RSI·SMA·ATR·MACD·Bollinger·RV), 결정용 신호(`rsi-divergence`·`sma-cross`·`bollinger-breakout`), `required_factors` 훅 사용 방식, lookahead guard 규칙. `[[13-feature-alpha-catalog]]` 참조.
5. **전략 포트폴리오** — MomoBtcV2 (라이브 대상), 차후 KRX Value/Momentum/Quality 후보 리스트. 각 전략별 AC·검증 Sharpe 목표.
6. **포지션 사이징** — Kelly·Fractional Kelly·Vol Targeting 중 어떤 걸 디폴트로, fraction 값, ATR·EWMA σ 입력, `sizing_mode` 선택 기준. 금액 예: equity 1000만원 기준 1회 최대 노출 N%.
7. **리스크 룰(서킷 브레이커)** — `docs/specs/risk-rule-dsl.md` 의 DSL 항목을 **실제 YAML 샘플** 로 인스턴스화. per_trade 1%, per_day 3%, MDD 5% halt, leverage 1.0 cap, sector 25%, 개별 한도 값 명시. `max-drawdown-5pct` · `kill-switch-runbook` 위키링크.
8. **포트폴리오 리스크** — LW Σ 추정·CVaR 95%·ENB·평균 ρ 임계값. 경보 트리거 → Kill Switch 연계 흐름.
9. **실행·브로커** — Binance(crypto) + KIS(KRX) 이원화. 주문 타입 매핑, TWAP/VWAP/Market/Limit 선택 규칙, 슬리피지·수수료 설정값. `docs/background/10-broker-api-comparison.md` 결정 반영.
10. **배포·운영** — **EXE 패키징** 구체안:
    - Tech 선택: PyInstaller (단일 .exe) vs Nuitka (컴파일 배포) vs Docker. 근거 간단 비교 + 결정.
    - 구조: CLI daemon + (선택) 로컬 웹 대시보드 (FastAPI + Prometheus 메트릭 endpoint)
    - 인증 키 저장: Windows Credential Manager / DPAPI
    - 로그·관측성: Prometheus + Grafana (`docker-compose.yml` 기존 스택 활용)
    - 업데이트 채널: GitHub Release 기반 체크섬 검증
    - 런타임 요구: Windows 10+, Python 미포함(단일 exe)
11. **단계별 로드맵 & 구현 진척도** — Paper-to-Live 단계 (`[[29-paper-to-live-protocol]]` Phase 0~4) 로 매핑:
    - 표 1: 각 Phase 의 진입 조건·완료 기준·예상 기간
    - 표 2: 이슈별 현황 — 머지된 이슈(#19/20/24/25/26/27/47/48/51/53/55/59/62/67/68/69/70/71) 와 열린 이슈(#73/#74/#75/#76/#77/#78/#79/#80/#81) 상태·Phase 맵핑
    - 목표: \"지금 우리는 Phase 0 완료, Phase 1 Shadow Paper 진입 직전\" 식으로 한 줄 요약

### 작성 순서
1. 빈 헤더 + 프론트매터로 뼈대 커밋 → CI 통과 확인
2. 섹션 2/11 (리서치·진척도) 먼저 — 기존 자료 집계 위주, 창작 적음
3. 섹션 6/7/10 (사이징 금액·리스크 DSL·EXE) — 구체 수치 결정 필요, 별도 세션에서 사용자와 결정 포인트 인터뷰
4. 나머지 섹션 채우기
5. AGENTS.md 링크 추가

### 스코프 밖 (다른 이슈)
- 진척도 자동 갱신 스크립트 (별도 chore 이슈 후보)
- Whitepaper v0.2 (라이브 데이터 축적 후)

## 개발 체크리스트
- [x] 해당 디렉토리 .ai.md 최신화 (`docs/whitepaper/.ai.md` 신규)



## 작업 내역

### 2026-04-27 — 완료

**현황**: 5/5 AC + 1/1 개발 체크리스트 모두 충족
**산출물**:
- `docs/whitepaper/qta-master-plan-v01.md` (1,007줄, CI strict 통과 — 113 노트 검증)
- `docs/whitepaper/.ai.md` 신규 작성
- `docs/whitepaper/_drafts/*.draft.md` 5개 (다관점 워커 초안 보존, CI 제외, Git 미커밋)
- `AGENTS.md` "핵심 문서 링크" 섹션에 백서 포인터 추가
**팀 작업 흐름**: `/team 5` 로 5인 다관점 워커(트레이더·개발자·마케터·브랜딩·VC 분석가) 병렬 작성 → CEO 통합 → 브랜드 톤 가이드(R9 산문 4줄 이내) 적용으로 1,143 → 1,007줄 압축
**v0.1 후속 도출**: 부록 B Known Concerns + VC 검증 체크리스트에서 21건 신규 백로그(#119-#140) 도출 + 사용자 지정 최종 목표(월 10% 수익) 반영해 백서 §1-10 갱신 + #119 P0 블로커 지정.

---

### 2026-04-26 — 플랜 작성

**현황**: 0/5 완료 (플랜 작성 단계)
**완료된 항목**:
- (없음)
**미완료 항목**:
- `docs/whitepaper/qta-master-plan-v01.md` 신규 작성 (프론트매터 스키마 준수)
- 11개 섹션 본문 채움 (위키링크 + 구체 수치)
- "구현 진척도" 섹션 표 정리
- CI `check_invariants.py --strict` 통과
- AGENTS.md '핵심 문서 링크' 섹션에 항목 추가
- `docs/whitepaper/.ai.md` 신규
**변경 파일**: 1개 (`01_plan.md` 전체 재작성 — 64개 이슈·32개 리서치 노트 정독 후 엔드투엔드 + 평어 위주로 갱신)

**작업 메모**:
- `/plan` 으로 플랜 전면 재작성. 사용자 추가 요구사항 2건 반영:
  1. 끝난·진행·백로그 이슈 + 리서치 문서 모두 정독 후 "프로젝트 최종 모습" 까지 엔드투엔드 기획
  2. 기술·수학 용어 보다 기능 위주 평어
- 3개 Explore agent 병렬 실행으로 디제스트 확보 (이슈 64건, 리서치 노트 32건, 현재 src 상태 7개 영역).
- 플랜에 §0 "기획서가 끝났을 때의 모습", §1 "사용자 운영 시나리오 (Phase 4 도달 시점)" 신설.
- 사용자 결정 인터뷰 5건 (사이징 N%, 리스크 한도값, EXE 도구, 키 저장, 현 단계 표현) 디폴트 제안과 함께 정리. Phase D 본문 작성 전 일괄 수령 예정.
