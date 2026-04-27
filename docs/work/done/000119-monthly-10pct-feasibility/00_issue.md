# research: 월 10% 수익률 목표 가능성 평가 + 전략·리스크·사이징 재설계

## 배경
사용자 지정 최종 목표 **월 10% (연환산 ~213% 복리)** 는 v0.1 보수 정책(레버리지 1.0x, MDD 5% halt, Sharpe ≥ 1.0) 과 충돌. 백서 §1-10 갱신본 참조.

## Phase / 월 10% 컨텍스트
- Phase 0-4 모든 후속 전략·리스크·사이징 결정의 기초
- 본 이슈 = 월 10% 가능성 직접 평가 (P0 최우선)

## AC
- [x] 최상위 펀드(Renaissance Medallion·Two Sigma·Citadel) 수익률·Sharpe 벤치마크 수치 정리
- [x] 월 10% 달성 수학 조건 4가지 정량화: 필요 Sharpe / 필요 레버리지 / MDD 허용 / 거래 빈도(LFT vs MFT vs HFT)
- [x] 현 카탈로그 5종 + 메타라벨러 ON + 레버리지 3-5x 시나리오 백테스트 (3년)
- [x] 미달 시 보강 옵션 3가지 제안 (신규 전략·MFT/HFT·옵션 활용 등)
- [x] (사용자 결정 대기) 사용자 권고: (a) 목표 유지 + 공격 정책 도입, (b) 목표 하향 + 보수 유지, (c) 단계별 차등 중 선택 요청
- [x] 결과 노트 docs/background/36-monthly-10pct-feasibility.md 작성

## 의존성·참고
- 후행: P1 전략·리스크 이슈 다수 의존
- 백서 §1-10, §5, §6, §7 / 부록 B-1

## 작업 내역

### 2026-04-27

**현황**: 6/6 완료 — AC5 사용자 결정 대기 (이슈 OPEN 유지)
**완료된 항목**: AC1, AC2, AC3, AC4, AC5(권고 작성 완료·결정 대기), AC6
**미완료 항목**: AC5 사용자 결정 (a/b/c 선택)

**변경 파일**: 6개
- `01_plan.md` 신규 (7단계 Task Flow + Guardrails)
- `02_research.md` 신규 — AC1 헤지펀드 벤치마크 + AC2 수학 4조건 (파이썬 재계산 포함)
- `02_implementation_catalog_3y.md` 신규 — AC3 카탈로그 5종 × DRY-RUN 3년 측정 결과
- `scripts/measure_strategy_catalog_3y.py` 신규 — 3년 윈도우 측정 스크립트
- `scripts/leverage_scenario.py` 신규 — 레버리지 시나리오 계산
- `tests/test_leverage_scenario.py` 신규 — 레버리지 회귀 테스트
- `docs/background/36-monthly-10pct-feasibility.md` 신규 (AC6)

**핵심 발견**:
- Medallion net 39%/년 = 월 2.77% — 월 10% 목표의 1/4 수준. 세계 최고 펀드도 월 10% 달성 사례 없음.
- 현 카탈로그 최고 Sharpe: momo_vol_filtered 1.102 (DRY-RUN). 월 10% 달성에 최소 L≈4× 필요.
- 현 MDD halt −5% 는 월 10% 경로에서 상시 발동 → 최소 −21% 완화 필요 (Calmar 10 기준).
- 보강 옵션: (A) 통계차익 신규 전략, (B) #111–114 OrderRouter/VWAP 인프라 (최우선), (C) 옵션 변동성 수익.
- 권고 a/b/c 비교표 작성 완료. 사용자 결정 후 #105/#107 정책 반영 예정.

**기록**:
- `01_plan.md` 작성 (7단계 Task Flow + Guardrails). 사전 조사: 기존 볼트에 Renaissance/Two Sigma/Citadel 정량 수치 부재 → 신규 노트 정당. 의존 이슈 12건 실측 — #79/#80/#85/#94/#95/#106 closed, #105/#107/#120/#121/#122/#138/#142 open. 백서는 빈 디렉토리 → 이슈 body "백서 §1-10" 표현은 미래형.
- researcher (task #1): AC1 벤치마크 5펀드 + KOSPI 파이썬 재계산. AC2 수학 4조건 재계산 증적.
- backtester (task #2): 카탈로그 5종 DRY-RUN 3년 측정. leverage_scenario.py + test 신규. measure_strategy_catalog_3y.py 신규.
- synthesizer (task #3): `36-monthly-10pct-feasibility.md` 작성 (7섹션, 위키링크 8종). AC1–6 체크 완료. AC5 사용자 결정 대기로 이슈 OPEN 유지.

### 2026-04-27 (사용자 (d) Sleeve allocation 방향 결정 후 보강)

**결정 요약**:
- 사용자가 단일 a/b/c 대신 **(d) Sleeve allocation (multi-PM 구조)** 채택. Sleeve 비중은 미정 (70/20/10 권장).
- 영향 받는 spec/research 문서 일괄 갱신 — sleeve 방향이 다른 노트의 v3 로드맵·SOP 와 정합되도록.

**추가 변경 파일** (5개):
- `02_implementation.md` §3.7 추가 — Sleeve allocation 분석적 합성 시뮬 (Phase 1 4개 비중 시나리오 + Phase 3 격상 후 통합 metrics).
- `36-monthly-10pct-feasibility.md` §6 (d) 옵션 추가 + §7 결정 요청 형식 변경 (단일 a/b/c → sleeve 비중 결정). 권고 갱신: (c) → **(d) 70/20/10 Phase 1**.
- `19-portfolio-risk.md` §6 v3 로드맵에 "Sleeve allocation" 항목 추가 (sleeve 별 독립 Policy + portfolio-level ENB/CVaR 측정).
- `20-position-sizing.md` §7 SOP 에 §7.1.5 Sleeve allocation 사이징 추가 (sleeve 별 독립 4단계 사이징).
- `risk-rule-dsl.md` §8 v3.1 Sleeve allocation 확장 명시 — `sleeve_id` 필드, sleeve 별 독립 `Policy`, `halt_sleeve` 액션, YAML 예시 1개.

**Sleeve 분석 핵심 (분석적 합성)**:
- Sleeve A (5전략 등가중 L=1) + Sleeve B (momo-vol-filtered L=3, fund 7.3%) + Sleeve C (Phase 게이트) 구조.
- **권장 비중 70/20/10 (Phase 1)**: 통합 연 수익 14.46%, Sharpe ~1.03, MDD 상한 −25%. Phase 3 게이트 통과 시 70/30 으로 자연 전환 → 연 18.55%, Sharpe ~1.04, MDD 상한 −30%.
- 월 10% (연 213.8%) 직접 도달은 어떤 비중에서도 불가. 단, sleeve B 단독 hit ratio 40.54% 가 자본의 30% 까지 격상 가능.

**선결 조건 (사용자 결정 후 별도 PR)**:
- `risk-rule-dsl` v3.1 — `sleeve_id` 필드 + sleeve 별 독립 `Policy` 인스턴스 + `halt_sleeve` 액션 추가
- 5전략 등가중 portfolio daily returns 시계열 실측 (sleeve A 의 σ·MDD·Sharpe 정확치 갱신)
- #105/#107 정책 PR 에 sleeve 별 한도 분리 적용
- #120 watchdog 의 sleeve 별 alarm 분기

**다음 단계**: 사용자가 sleeve 비중 (70/20/10 권장 또는 기타) 선택 → 본 이슈 close → `risk-rule-dsl` v3.1 확장 PR 착수.

### 2026-04-27 (사용자 비중 보류 + 전략 확장 우선 결정)

**사용자 결정 (확정)**:
- **(d) Sleeve allocation 방향 채택**.
- **Sleeve 비중은 신규 전략 추가 후 재평가** ("전략을 추가하면서 수익률을 끌어올린다. 멀티 전략 프로그램 컨셉을 유지").
- **본 이슈 #119 close 가능** — AC5 결정 완료, 비중 보류.

**백서 정합성 확인**:
- 기획서 `docs/whitepaper/qta-master-plan-v01.md` 는 `chore/000133-phase2-operation` 브랜치 (PR #141, master 미머지) 에 존재. 본 워크트리에는 보이지 않음 — 별도 PR 머지 후 가용.
- §1-8 "5종 전략 카탈로그" + §1-10 "신규 전략 발굴 후 v0.2 갱신" + §5-7 "#99 VWMA 단타 카탈로그 사전 등록" — 멀티 전략 + 카탈로그 확장 = 백서의 핵심 컨셉. 본 이슈 결과 (sleeve + 비중 보류) 와 정합.
- 본 이슈 결과가 백서 v0.2 (#138, #142) 의 §1-10 / §5-7 입력으로 흡수될 것.

**`36-monthly-10pct-feasibility.md` §7 갱신**:
- 비중 박스 "신규 전략 추가 후 재평가 (보류)" 로 변경
- 결정 근거에 백서 §1-10 인용 추가
- 후속 조치 8항 명시 (1: risk-rule-dsl v3.1, 2: 신규 전략, 3: 실측 갱신, 4-7: 후행 이슈, 8: 백서 v0.2 반영)

**즉시 실행 가능한 후속 작업**:
1. **신규 이슈 — `risk-rule-dsl` v3.1 sleeve 확장**: `sleeve_id` 필드 + 독립 Policy + `halt_sleeve` 액션. 비중 무관하게 진행.
2. **#99 VWMA 단타 카탈로그 구현 가속화** (이미 OPEN) — 6번째 전략 추가가 sleeve B 알파 보강.
3. **신규 이슈 — 5전략 등가중 portfolio daily returns 실측 + sleeve A 정확치 갱신**.

**본 이슈 close 권고**: `/finish-issue` 또는 `/fi 119` 로 PR 생성. PR 본문에 "AC5 결정: (d) 채택, 비중 보류 (전략 확장 후 재평가)" 명시.

